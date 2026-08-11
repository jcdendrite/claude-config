"""Tests for _stow_migration_lib.sh's resumable-failure and trust-boundary
contract. Sources the real library into a bash subprocess and calls
stow_migrate_adopted_dir directly against a fake $HOME and repo, the same
sourcing pattern hooks/tests/test_marker_lib.py uses for _lib.sh.
"""
from __future__ import annotations

import os
import re
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


def _run_unadopt(repo_dir: Path, name: str, home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'. "{LIB_SH}"; stow_unadopt_entry "$1" "$2"',
         "run_unadopt", str(repo_dir), name],
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


def _run_newest_backup(name: str, home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'. "{LIB_SH}"; _stow_migration_lib_newest_backup "$1"',
         "run_newest_backup", name],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home)},
    )


class TestNewestBackupNameBoundary:
    """_stow_migration_lib_newest_backup shares _newest_unadopt_run's
    unbounded-glob shape (no [0-9] boundary after the dot) -- not reachable
    through today's only callers (plans/handoffs/briefs, none a dot-prefix
    of another) but fixed identically per the same bug class, mirroring
    TestNewestUnadoptRunNameBoundary above."""

    def test_lookup_does_not_match_a_dot_prefixed_sibling_backup_dir(
        self, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        backup_root = home / ".claude-config-relocate-backup"
        own_backup = backup_root / "foo.20260101000000"
        own_backup.mkdir(parents=True)
        (own_backup / "f.md").write_text("own\n")
        # A newer backup for an unrelated name ("bar") that happens to be
        # dot-prefixed with "foo" -- lexicographically sorts ahead of
        # own_backup under `sort -r`, so an unescaped/unbounded glob would
        # wrongly return this one instead.
        sibling_backup = backup_root / "foo.bar.20260102000000"
        sibling_backup.mkdir(parents=True)
        (sibling_backup / "b.md").write_text("sibling\n")

        result = _run_newest_backup("foo", home)

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(own_backup), (
            f"a lookup for 'foo' must resolve to its own backup, not the "
            f"newer dot-prefixed sibling {sibling_backup}; got {result.stdout!r}"
        )


def _run_newest_unadopt_run(name: str, home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'. "{LIB_SH}"; _stow_migration_lib_newest_unadopt_run "$1"',
         "run_newest_unadopt_run", name],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home)},
    )


class TestNewestUnadoptRunNameBoundary:
    """_stow_migration_lib_newest_unadopt_run's glob must not let a lookup
    for one name consume another name's run directory just because that
    other name is dot-prefixed with the first -- mirrors
    TestNewestBackupSelection's construction below for the sibling
    _stow_migration_lib_newest_backup, adapted to the "-unadopt" suffix."""

    def test_lookup_does_not_match_a_dot_prefixed_sibling_run_directory(
        self, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        backup_root = home / ".claude-config-relocate-backup"
        own_run_dir = backup_root / "foo.20260101000000-unadopt"
        own_run_dir.mkdir(parents=True)
        # A newer run directory for an unrelated name ("bar") that happens
        # to be dot-prefixed with "foo" -- lexicographically sorts ahead of
        # own_run_dir under `sort -r`, so an unescaped/unbounded glob would
        # wrongly return this one instead.
        sibling_run_dir = backup_root / "foo.bar.20260102000000-unadopt"
        sibling_run_dir.mkdir(parents=True)

        result = _run_newest_unadopt_run("foo", home)

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(own_run_dir), (
            f"a lookup for 'foo' must resolve to its own run directory, not "
            f"the newer dot-prefixed sibling {sibling_run_dir}; "
            f"got {result.stdout!r}"
        )

    def test_lookup_does_not_match_a_sibling_starting_with_a_single_digit(
        self, tmp_path: Path
    ) -> None:
        """A narrower collision than the dot-prefixed-sibling case above:
        `[0-9]*` (one digit then anything) still let a lookup for "foo1"
        cross-match "foo1.2bar.<ts>-unadopt", since "2" alone satisfies "one
        digit" -- the exact 14-digit-width glob (_STOW_MIGRATION_LIB_TIMESTAMP_GLOB)
        is required to close it fully."""
        home = tmp_path / "home"
        backup_root = home / ".claude-config-relocate-backup"
        own_run_dir = backup_root / "foo1.20260101130000-unadopt"
        own_run_dir.mkdir(parents=True)
        # An unrelated name "foo1.2bar" -- its run directory starts with a
        # single digit right after "foo1.", which satisfies a "[0-9]*" glob
        # but not the real 14-digit timestamp width.
        sibling_run_dir = backup_root / "foo1.2bar.20260101120000-unadopt"
        sibling_run_dir.mkdir(parents=True)

        result = _run_newest_unadopt_run("foo1", home)

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(own_run_dir), (
            f"a lookup for 'foo1' must resolve to its own run directory, not "
            f"the digit-prefixed sibling {sibling_run_dir}; got {result.stdout!r}"
        )


class TestNewestUnadoptRunSameNameSelection:
    """_stow_migration_lib_newest_unadopt_run must pick the newest of
    several genuine run directories for the SAME name -- mirrors
    TestNewestBackupSelection's newest-of-several-candidates coverage for
    the sibling _stow_migration_lib_newest_backup, which had no equivalent
    for this function."""

    def test_resolves_to_the_later_of_two_same_name_run_directories(
        self, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        backup_root = home / ".claude-config-relocate-backup"
        older_run_dir = backup_root / "foo.20260101000000-unadopt"
        older_run_dir.mkdir(parents=True)
        newer_run_dir = backup_root / "foo.20260102000000-unadopt"
        newer_run_dir.mkdir(parents=True)

        result = _run_newest_unadopt_run("foo", home)

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(newer_run_dir), (
            f"a lookup for 'foo' must resolve to the later of its two run "
            f"directories, not {result.stdout!r}"
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


class TestStowUnadoptEntry:
    """Tests for stow_unadopt_entry, the rename-based un-adopt function that
    replaces a copy for the ~36 names stow --adopt pulled in with no
    migration of their own -- unlike stow_migrate_adopted_dir above, it
    works on plain files as well as directories, and there is no
    partially-populated-target state to disambiguate: a rename on the same
    filesystem either has run or hasn't."""

    def test_directory_shape_happy_path_renames_into_place(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        repo = tmp_path / "repo"
        source = repo / "claude" / ".claude" / "projects"
        source.mkdir(parents=True)
        (source / "p.md").write_text("# projects fixture\n")
        target = home / ".claude" / "projects"
        target.symlink_to(source)

        result = _run_unadopt(repo, "projects", home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert target.is_dir() and not target.is_symlink()
        assert (target / "p.md").read_text() == "# projects fixture\n"
        assert not source.exists(), (
            "a rename must leave nothing behind at the package-side source"
        )

    def test_plain_file_shape_happy_path_renames_into_place(self, tmp_path: Path) -> None:
        """No existing test anywhere in this module exercises a file-shaped
        entry -- stow_migrate_adopted_dir's `cd -P` (via
        _stow_migration_lib_symlink_resolves_to) cannot even resolve one,
        which is exactly why this function exists."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        repo = tmp_path / "repo"
        (repo / "claude" / ".claude").mkdir(parents=True)
        source = repo / "claude" / ".claude" / ".claude.json"
        source.write_text('{"fixture": true}')
        target = home / ".claude" / ".claude.json"
        target.symlink_to(source)

        result = _run_unadopt(repo, ".claude.json", home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert target.is_file() and not target.is_symlink()
        assert target.read_text() == '{"fixture": true}'
        assert not source.exists()

    def test_interrupted_between_unlink_and_rename_resumes_a_directory(
        self, tmp_path: Path
    ) -> None:
        """A prior run can fail after unlinking the symlink but before the
        rename, leaving ~/.claude/<name> absent entirely with a run
        directory it created but never marked complete. A later run must
        detect this as resumable and complete the rename from the
        package-side source -- not treat a missing target as nothing to
        migrate."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        repo = tmp_path / "repo"
        source = repo / "claude" / ".claude" / "projects"
        source.mkdir(parents=True)
        (source / "p.md").write_text("# projects fixture\n")
        run_dir = home / ".claude-config-relocate-backup" / "projects.20260101000000-unadopt"
        run_dir.mkdir(parents=True)

        result = _run_unadopt(repo, "projects", home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        target = home / ".claude" / "projects"
        assert target.is_dir() and not target.is_symlink()
        assert (target / "p.md").read_text() == "# projects fixture\n"
        assert not source.exists()
        assert (run_dir / MIGRATION_COMPLETE_SENTINEL).exists()

    def test_interrupted_between_unlink_and_rename_resumes_a_plain_file(
        self, tmp_path: Path
    ) -> None:
        """Same resume window as the directory case above, for a
        file-shaped entry -- the two shapes take the same code path in
        stow_unadopt_entry, but nothing else in this module proves that for
        the resume branch specifically."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        repo = tmp_path / "repo"
        (repo / "claude" / ".claude").mkdir(parents=True)
        source = repo / "claude" / ".claude" / ".claude.json"
        source.write_text('{"fixture": true}')
        run_dir = home / ".claude-config-relocate-backup" / ".claude.json.20260101000000-unadopt"
        run_dir.mkdir(parents=True)

        result = _run_unadopt(repo, ".claude.json", home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        target = home / ".claude" / ".claude.json"
        assert target.is_file() and not target.is_symlink()
        assert target.read_text() == '{"fixture": true}'
        assert not source.exists()
        assert (run_dir / MIGRATION_COMPLETE_SENTINEL).exists()

    def test_second_call_after_completion_is_a_silent_noop(self, tmp_path: Path) -> None:
        """Mirrors TestPartialStepCCopyResumability's empty-stdout/stderr
        assertion for stow_migrate_adopted_dir's own idempotent no-op."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        repo = tmp_path / "repo"
        source = repo / "claude" / ".claude" / "projects"
        source.mkdir(parents=True)
        (source / "p.md").write_text("# projects fixture\n")
        target = home / ".claude" / "projects"
        target.symlink_to(source)

        first = _run_unadopt(repo, "projects", home)
        assert first.returncode == 0, first.stderr

        second = _run_unadopt(repo, "projects", home)

        assert second.returncode == 0
        assert second.stdout == "" and second.stderr == "", (
            f"a no-op second run must be silent; stdout={second.stdout!r} "
            f"stderr={second.stderr!r}"
        )
        assert (target / "p.md").read_text() == "# projects fixture\n"

    def test_preplanted_backup_root_symlink_refuses_rather_than_writes_through_it(
        self, tmp_path: Path
    ) -> None:
        """Reproduces TestBackupRootSymlinkGuard's repro against
        stow_unadopt_entry specifically -- the guard code is unchanged, but
        that doesn't prove this function calls it before its own unlink
        step."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        repo = tmp_path / "repo"
        source = repo / "claude" / ".claude" / "projects"
        source.mkdir(parents=True)
        (source / "secret.md").write_text("secret\n")
        target = home / ".claude" / "projects"
        target.symlink_to(source)

        elsewhere = tmp_path / "attacker-owned"
        elsewhere.mkdir()
        (home / ".claude-config-relocate-backup").symlink_to(elsewhere)

        result = _run_unadopt(repo, "projects", home)

        assert result.returncode == 1
        assert "already exists as a symlink" in result.stderr
        assert list(elsewhere.iterdir()) == [], (
            "un-adopt must not write through a pre-planted symlink at the "
            "shared backup root"
        )
        assert target.is_symlink(), "refusal must happen before the target is unlinked"
        assert source.exists(), (
            "refusal must happen before the package-side source is renamed away"
        )

    def test_backup_root_symlink_guard_runs_before_any_lookup_beneath_it(
        self, tmp_path: Path
    ) -> None:
        """The guard must refuse before _stow_migration_lib_newest_unadopt_run
        ever globs through a hijacked backup-root -- a planted run directory
        with a fake completion sentinel there must not let the function
        silently report "nothing to do" instead of refusing."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        repo = tmp_path / "repo"
        (repo / "claude" / ".claude").mkdir(parents=True)
        elsewhere = tmp_path / "attacker-owned"
        fake_run_dir = elsewhere / "plans.99999999999999-unadopt"
        fake_run_dir.mkdir(parents=True)
        (fake_run_dir / MIGRATION_COMPLETE_SENTINEL).touch()
        (home / ".claude-config-relocate-backup").symlink_to(elsewhere)

        result = _run_unadopt(repo, "plans", home)

        assert result.returncode == 1, (
            "a hijacked backup-root symlink must be refused, not silently "
            f"treated as a legitimate completed run; stdout={result.stdout!r} "
            f"stderr={result.stderr!r}"
        )
        assert "already exists as a symlink" in result.stderr

    def test_real_target_with_no_run_dir_is_left_untouched(self, tmp_path: Path) -> None:
        """The documented no-op contract: a real, non-symlink $target this
        function never adopted (no run directory of its own) must not be
        touched, even though it happens to share a name with a package-side
        entry -- see stow_migrate_adopted_dir's own package-side leftovers
        for exactly this case in practice."""
        home = tmp_path / "home"
        repo = tmp_path / "repo"
        (repo / "claude" / ".claude").mkdir(parents=True)
        target = home / ".claude" / "plans"
        target.mkdir(parents=True)
        (target / "p.md").write_text("# already migrated by the other mechanism\n")

        result = _run_unadopt(repo, "plans", home)

        assert result.returncode == 0
        assert result.stdout == "" and result.stderr == ""
        assert (target / "p.md").read_text() == "# already migrated by the other mechanism\n"

    def test_mv_failure_reports_and_leaves_target_unlinked(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        repo = tmp_path / "repo"
        (repo / "claude" / ".claude").mkdir(parents=True)
        # Deliberately never created: the symlink resolves (both sides'
        # realpath match, since realpath doesn't require existence), so
        # target_is_live_symlink is true and the function proceeds to
        # unlink $target and then attempt the mv, which fails with ENOENT.
        source = repo / "claude" / ".claude" / "projects"
        target = home / ".claude" / "projects"
        target.symlink_to(source)

        result = _run_unadopt(repo, "projects", home)

        assert result.returncode == 1
        assert "could not rename" in result.stderr
        assert not target.exists() and not target.is_symlink(), (
            "target must be left unlinked, not a partial state"
        )

    def test_resumed_rename_refuses_to_clobber_content_that_reappeared_since_interruption(
        self, tmp_path: Path
    ) -> None:
        """The clobber this function must not commit: something else (Claude
        Code itself, on next launch, recreating a missing .claude.json)
        repopulates $target with independent real content during the window
        between an interrupted run and its resume. The resumed rename must
        refuse, not silently overwrite live content with the stale
        package-side copy."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        repo = tmp_path / "repo"
        (repo / "claude" / ".claude").mkdir(parents=True)
        source = repo / "claude" / ".claude" / ".claude.json"
        source.write_text('{"stale": true}')
        run_dir = home / ".claude-config-relocate-backup" / ".claude.json.20260101000000-unadopt"
        run_dir.mkdir(parents=True)
        target = home / ".claude" / ".claude.json"
        target.write_text('{"live": true}')

        result = _run_unadopt(repo, ".claude.json", home)

        assert result.returncode == 1, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "refusing to overwrite" in result.stderr
        assert target.read_text() == '{"live": true}', (
            "must not clobber independently-recreated content"
        )
        assert source.read_text() == '{"stale": true}', (
            "package-side copy must remain untouched"
        )
        assert not (run_dir / MIGRATION_COMPLETE_SENTINEL).exists()

    def test_resume_after_successful_rename_with_failed_sentinel_touch_self_heals(
        self, tmp_path: Path
    ) -> None:
        """A prior run's mv can succeed while the subsequent completion-
        sentinel touch fails (disk full, permissions). Without
        disambiguation, a naive resume would retry the mv against a source
        that's already gone, failing forever with a misleading "package-side
        entry is untouched" message even though the un-adopt already fully
        succeeded."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        repo = tmp_path / "repo"
        (repo / "claude" / ".claude").mkdir(parents=True)
        # expected_source already gone (the rename succeeded); target
        # already real (the rename succeeded); run_dir exists but was never
        # marked complete (the touch after mv failed).
        target = home / ".claude" / "projects"
        target.mkdir(parents=True)
        (target / "p.md").write_text("# already migrated\n")
        run_dir = home / ".claude-config-relocate-backup" / "projects.20260101000000-unadopt"
        run_dir.mkdir(parents=True)

        result = _run_unadopt(repo, "projects", home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert (run_dir / MIGRATION_COMPLETE_SENTINEL).exists(), (
            "must self-heal the missing sentinel rather than retry a rename "
            "whose source is already gone"
        )
        assert (target / "p.md").read_text() == "# already migrated\n"

    def test_neither_target_nor_source_present_after_interrupted_run_fails_loudly(
        self, tmp_path: Path
    ) -> None:
        """A state stow_unadopt_entry's own two-step unlink-then-rename
        should never produce on its own, but must not silently succeed or
        silently no-op if reached some other way -- both the rename's
        source and its destination are missing."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        repo = tmp_path / "repo"
        (repo / "claude" / ".claude").mkdir(parents=True)
        run_dir = home / ".claude-config-relocate-backup" / "projects.20260101000000-unadopt"
        run_dir.mkdir(parents=True)

        result = _run_unadopt(repo, "projects", home)

        assert result.returncode == 1
        assert "investigate" in result.stderr.lower()
        assert not (run_dir / MIGRATION_COMPLETE_SENTINEL).exists()


def _run_untracked_entries(repo_dir: Path, home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'. "{LIB_SH}"; stow_untracked_package_entries "$1"',
         "run_untracked_entries", str(repo_dir)],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home)},
    )


class TestStowUntrackedPackageEntries:
    """Tests for stow_untracked_package_entries, the git-ls-files-vs-find set
    difference shared by install.sh's un-adopt loop and its --ignore-arg
    construction."""

    def test_diffs_git_tracked_from_physically_present(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        repo = tmp_path / "repo"
        package_dir = repo / "claude" / ".claude"
        tracked = package_dir / "skills"
        tracked.mkdir(parents=True)
        (tracked / "example.md").write_text("# example\n")
        untracked = package_dir / "projects"
        untracked.mkdir(parents=True)
        (untracked / "session.json").write_text("{}")

        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "add", "claude/.claude/skills"], cwd=repo, check=True)

        result = _run_untracked_entries(repo, home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        names = [n for n in result.stdout.split("\x00") if n]
        assert names == ["projects"], (
            "must report the untracked entry and exclude the tracked one"
        )

    def test_git_failure_fails_loudly_instead_of_reporting_nothing_tracked(
        self, tmp_path: Path
    ) -> None:
        """Confirmed production risk: a git failure silently treated as
        "nothing tracked" would make the un-adopt loop rename real tracked
        package content (skills/, scripts/, ...) out of the checkout, and
        would make the --ignore construction ignore literally everything."""
        home = tmp_path / "home"
        repo = tmp_path / "repo"  # deliberately not a git repository
        (repo / "claude" / ".claude" / "skills").mkdir(parents=True)

        result = _run_untracked_entries(repo, home)

        assert result.returncode == 1
        assert result.stdout == "", "must not report any entries on a git failure"
        assert "could not list git-tracked files" in result.stderr

    def test_absent_package_dir_is_a_silent_noop(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        repo = tmp_path / "repo"  # claude/.claude doesn't exist at all
        repo.mkdir(parents=True)

        result = _run_untracked_entries(repo, home)

        assert result.returncode == 0
        assert result.stdout == "" and result.stderr == ""

    def test_dedicated_migration_names_are_never_reported_even_when_untracked(
        self, tmp_path: Path
    ) -> None:
        """plans/, handoffs/, and briefs/ are permanently git-untracked and
        have their own dedicated backup-before-touch migration path
        (stow_migrate_adopted_dir) -- if this function reported them too,
        the un-adopt loop could fall through to un-adopting one of them via
        a bare `mv` with no such backup when the dedicated path fails before
        unlinking the symlink."""
        home = tmp_path / "home"
        repo = tmp_path / "repo"
        package_dir = repo / "claude" / ".claude"
        for name in ("plans", "handoffs", "briefs"):
            entry = package_dir / name
            entry.mkdir(parents=True)
            (entry / "f.md").write_text(f"# {name} fixture\n")
        other_untracked = package_dir / "projects"
        other_untracked.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        # Deliberately nothing `git add`ed -- reproduces the real repo's
        # state, where these three names are never tracked at all.

        result = _run_untracked_entries(repo, home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        names = [n for n in result.stdout.split("\x00") if n]
        assert names == ["projects"], (
            f"plans/handoffs/briefs must be excluded even though they are "
            f"physically present and untracked; got {names}"
        )

    def test_tracked_name_requiring_git_c_quoting_still_extracts_correctly(
        self, tmp_path: Path
    ) -> None:
        """A tracked top-level directory name containing a literal double
        quote is exactly the shape `git ls-files` (without -z) C-quotes --
        the defect class the -z rewrite exists to prevent. Without -z, this
        name would arrive as `"skills\\"weird"/example.md` and corrupt the
        `#*/`/`%%/*` top-level-name extraction."""
        home = tmp_path / "home"
        repo = tmp_path / "repo"
        package_dir = repo / "claude" / ".claude"
        tracked = package_dir / 'skills"weird'
        tracked.mkdir(parents=True)
        (tracked / "example.md").write_text("# example\n")
        untracked = package_dir / "projects"
        untracked.mkdir(parents=True)
        (untracked / "session.json").write_text("{}")

        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "add", 'claude/.claude/skills"weird'], cwd=repo, check=True)

        result = _run_untracked_entries(repo, home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        names = [n for n in result.stdout.split("\x00") if n]
        assert names == ["projects"], (
            f"the quote-containing tracked name must still be correctly "
            f"excluded (not misparsed into reporting 'projects' plus a "
            f"corrupted extra entry); got {names}"
        )


class TestGlobEscapeAdversarial:
    """_stow_migration_lib_glob_escape is exercised only by benign names
    elsewhere in this module -- these pin that a name containing a literal
    glob metacharacter resolves, via _stow_migration_lib_newest_unadopt_run,
    to its own run directory and not a decoy sibling an unescaped glob would
    wrongly consume."""

    @pytest.mark.parametrize(
        ("name", "decoy"),
        [
            ("log*name", "logXYZname"),
            ("file?name", "fileZname"),
            ("cache[1]", "cache1"),
        ],
    )
    def test_metacharacter_in_name_does_not_glob_match_a_decoy_sibling(
        self, tmp_path: Path, name: str, decoy: str
    ) -> None:
        home = tmp_path / "home"
        backup_root = home / ".claude-config-relocate-backup"
        exact = backup_root / f"{name}.20260101000000-unadopt"
        exact.mkdir(parents=True)
        # A newer decoy directory named as if the metacharacter in `name`
        # had been left unescaped and interpreted as a wildcard -- if
        # escaping is broken, this wins the lexicographic sort instead.
        decoy_dir = backup_root / f"{decoy}.20260102000000-unadopt"
        decoy_dir.mkdir(parents=True)

        result = _run_newest_unadopt_run(name, home)

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(exact), (
            f"an unescaped metacharacter in {name!r} would let the newer "
            f"decoy {decoy_dir} wrongly win the lexicographic sort; "
            f"got {result.stdout!r}"
        )


class TestRegexEscapeAdversarial:
    """_stow_migration_lib_regex_escape backs install.sh's `stow --ignore`
    pattern construction (an anchored regex embedding a filesystem-derived
    name) -- exercised elsewhere only for literal-dot escaping. These pin
    that a name containing a regex metacharacter, embedded in the same
    anchored '^...$' shape install.sh builds, matches only itself and not a
    decoy sibling the metacharacter would wrongly match if left unescaped."""

    @pytest.mark.parametrize(
        ("name", "decoy"),
        [
            ("a+b", "aaab"),
            ("a|b", "a"),
            ("(ab)", "ab"),
        ],
    )
    def test_metacharacter_in_name_does_not_regex_match_a_decoy_sibling(
        self, name: str, decoy: str
    ) -> None:
        result = subprocess.run(
            ["bash", "-c", f'. "{LIB_SH}"; _stow_migration_lib_regex_escape "$1"',
             "run_regex_escape", name],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr
        # subprocess.run does not strip trailing output the way bash's own
        # `$(...)` capture does (which is what install.sh actually uses) --
        # strip the trailing newline python3's own print() adds so it isn't
        # embedded in the pattern below.
        escaped = result.stdout.rstrip("\n")
        pattern = f"^{escaped}$"

        assert re.fullmatch(pattern, name), (
            f"escaped pattern {pattern!r} must still match the exact name {name!r}"
        )
        assert not re.fullmatch(pattern, decoy), (
            f"an unescaped metacharacter in {name!r} would let the escaped "
            f"pattern also match the decoy sibling {decoy!r}"
        )
