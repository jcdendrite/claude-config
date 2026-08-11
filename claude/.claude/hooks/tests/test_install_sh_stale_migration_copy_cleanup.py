"""Tests for install.sh's stale-migration-copy cleanup step -- the
operator-confirmed deletion of claude/.claude/<name> leftovers that
stow_migrate_adopted_dir's copy-then-restore never removes for plans/,
handoffs/, and briefs/.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from helpers import SCRIPTS_DIR

_INSTALL_SH = Path(__file__).resolve().parents[4] / "install.sh"
_BASH = shutil.which("bash") or "/bin/bash"

_FIXTURE_START = "# INSTALL_TEST_FIXTURE: stale-migration-copy-cleanup — start\n"
_FIXTURE_END = "# INSTALL_TEST_FIXTURE: stale-migration-copy-cleanup — end"

# Mirrors _STOW_MIGRATION_COMPLETE_SENTINEL in _stow_migration_lib.sh --
# stow_migration_is_complete (which _report_and_clean_stale_migration_copy
# calls) reads this exact filename inside stow_migrate_adopted_dir's own
# backup directory to tell a fully-restored migration apart from a
# partially-restored one.
MIGRATION_COMPLETE_SENTINEL = ".migration-complete"


def _seed_completed_migration_backup(home: Path, name: str) -> Path:
    """Create the backup directory stow_migrate_adopted_dir leaves behind
    after a fully successful migration -- the only state
    stow_migration_is_complete trusts as "complete." Bare target content
    (without this) is exactly the ambiguous, partially-restored state
    _report_and_clean_stale_migration_copy must not treat as safe to
    offer for deletion."""
    backup_dir = home / ".claude-config-relocate-backup" / f"{name}.20260101000000"
    backup_dir.mkdir(parents=True)
    (backup_dir / MIGRATION_COMPLETE_SENTINEL).touch()
    return backup_dir


def _extract_cleanup_block() -> str:
    """Same marker-delimited extraction strategy as the other
    test_install_sh_*.py files."""
    install_text = _INSTALL_SH.read_text()
    start = install_text.find(_FIXTURE_START)
    assert start != -1, f"{_FIXTURE_START!r} not found in {_INSTALL_SH}"
    end = install_text.find(_FIXTURE_END, start)
    assert end != -1, f"{_FIXTURE_END!r} not found after start marker in {_INSTALL_SH}"
    block = install_text[start + len(_FIXTURE_START) : end]
    assert (
        "_report_and_clean_stale_migration_copy" in block
        and "_prompt_delete_stale_migration_copy" in block
    ), (
        f"extracted block is missing a function; markers in {_INSTALL_SH} are "
        f"probably misplaced. Got: {block!r}"
    )
    return block


def _run_prompt_delete(
    stale_path: Path, target: Path, stdin_text: str
) -> subprocess.CompletedProcess:
    """Call _prompt_delete_stale_migration_copy directly, bypassing the
    `[ -t 0 ]` gate that only wraps _report_and_clean_stale_migration_copy --
    same technique test_install_sh_machine_level_opt_ins.py uses for
    _prompt_sentinel_opt_in."""
    script = (
        "set -e\n"
        + _extract_cleanup_block()
        + f'\n_prompt_delete_stale_migration_copy "{stale_path}" "{target}"\n'
    )
    return subprocess.run(
        [_BASH, "-c", script],
        input=stdin_text,
        capture_output=True,
        text=True,
        check=False,
        env=dict(os.environ),
    )


def _run_report_and_clean(
    repo_dir: Path, name: str, home: Path, stdin: str
) -> subprocess.CompletedProcess:
    """Call _report_and_clean_stale_migration_copy, the real install.sh call
    site -- an empty stdin="" (piped but immediately EOF, never a tty)
    exercises the `[ -t 0 ]` gate's false branch, the same technique
    test_install_sh_machine_level_opt_ins.py's TTY-gate tests use and for
    the same reason: a pipe is never a tty regardless of what it carries.

    _report_and_clean_stale_migration_copy calls stow_migration_is_complete,
    which install.sh's own earlier (unextracted) sourcing line provides in
    production -- source it here instead, matching
    test_install_sh_stow_adopt_ignore.py's identical need for
    stow_untracked_package_entries."""
    script = (
        f'. "{SCRIPTS_DIR / "_stow_migration_lib.sh"}"\n'
        "set -e\n"
        + _extract_cleanup_block()
        + f'\n_report_and_clean_stale_migration_copy "{repo_dir}" "{name}"\n'
    )
    return subprocess.run(
        [_BASH, "-c", script],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "HOME": str(home)},
    )


class TestPromptDeleteStaleMigrationCopy:
    def test_confirmed_yes_deletes_the_path(self, tmp_path: Path) -> None:
        stale_path = tmp_path / "stale" / "plans"
        stale_path.mkdir(parents=True)
        (stale_path / "p.md").write_text("# stale plan\n")
        target = tmp_path / "target" / "plans"

        result = _run_prompt_delete(stale_path, target, "y\n")

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert not stale_path.exists(), "a confirmed 'y' must delete the stale path"

    def test_declined_leaves_the_path_in_place(self, tmp_path: Path) -> None:
        stale_path = tmp_path / "stale" / "plans"
        stale_path.mkdir(parents=True)
        (stale_path / "p.md").write_text("# stale plan\n")
        target = tmp_path / "target" / "plans"

        result = _run_prompt_delete(stale_path, target, "n\n")

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert stale_path.is_dir(), "a declined prompt must not delete the stale path"
        assert (stale_path / "p.md").read_text() == "# stale plan\n"

    def test_bare_enter_defaults_to_declined(self, tmp_path: Path) -> None:
        """The prompt's own [y/N] label documents Enter as "no" -- pins
        that the case statement's default arm actually matches an empty
        answer, not just an explicit "n"."""
        stale_path = tmp_path / "stale" / "plans"
        stale_path.mkdir(parents=True)
        target = tmp_path / "target" / "plans"

        result = _run_prompt_delete(stale_path, target, "\n")

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert stale_path.is_dir()


class TestReportAndCleanStaleMigrationCopy:
    def test_no_stale_copy_is_a_silent_noop(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        repo = tmp_path / "repo"
        (repo / "claude" / ".claude").mkdir(parents=True)

        result = _run_report_and_clean(repo, "plans", home, stdin="")

        assert result.returncode == 0
        assert result.stdout == "" and result.stderr == ""

    def test_target_still_a_live_symlink_is_left_untouched_and_unreported(
        self, tmp_path: Path
    ) -> None:
        """The safety-critical case: migration hasn't succeeded yet (or
        failed), so ~/.claude/plans is still a symlink into
        claude/.claude/plans -- that package-side directory is the
        symlink's only-copy target, not a stale duplicate, and reporting or
        offering to delete it would risk destroying the only copy of the
        user's content on a confirmed 'y'."""
        home = tmp_path / "home"
        repo = tmp_path / "repo"
        source = repo / "claude" / ".claude" / "plans"
        source.mkdir(parents=True)
        (source / "p.md").write_text("# only copy\n")
        target = home / ".claude" / "plans"
        target.parent.mkdir(parents=True)
        target.symlink_to(source)

        result = _run_report_and_clean(repo, "plans", home, stdin="y\n")

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert result.stdout == "" and result.stderr == "", (
            "a live, not-yet-migrated symlink must not even be reported, "
            f"let alone offered for deletion; stdout={result.stdout!r} "
            f"stderr={result.stderr!r}"
        )
        assert source.is_dir(), "the package-side content must survive untouched"
        assert (source / "p.md").read_text() == "# only copy\n"
        assert target.is_symlink()

    def test_non_interactive_stdin_reports_but_does_not_delete(self, tmp_path: Path) -> None:
        """A piped stdin (this test's own subprocess, and every CI run) is
        never a tty regardless of what it carries, so the confirmed-delete
        path itself is only reachable from a real terminal and is exercised
        directly against _prompt_delete_stale_migration_copy in
        TestPromptDeleteStaleMigrationCopy above -- this integration-level
        test instead pins that a completed migration's stale copy is
        reported (not silently skipped, per this cleanup step's own
        "report, then delete on confirmation" contract) and left in place
        under non-interactive stdin."""
        home = tmp_path / "home"
        repo = tmp_path / "repo"
        source = repo / "claude" / ".claude" / "plans"
        source.mkdir(parents=True)
        (source / "p.md").write_text("# stale copy\n")
        target = home / ".claude" / "plans"
        target.mkdir(parents=True)
        (target / "p.md").write_text("# migrated copy\n")
        _seed_completed_migration_backup(home, "plans")

        result = _run_report_and_clean(repo, "plans", home, stdin="")

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert source.is_dir(), "closed/non-tty stdin must not delete anything"
        assert str(source) in result.stderr, (
            f"a completed migration's stale copy must be reported; stderr={result.stderr!r}"
        )
        assert "not an interactive terminal" in result.stderr
        assert (target / "p.md").read_text() == "# migrated copy\n"

    def test_partially_restored_target_is_never_offered_for_deletion(
        self, tmp_path: Path
    ) -> None:
        """The bug this completeness check exists to close: a
        stow_migrate_adopted_dir run that copies some but not all files
        before being interrupted (disk full, kill -9) leaves ~/.claude/plans
        as a real, non-symlink directory -- indistinguishable from a
        completed migration by existence alone. Without consulting
        stow_migration_is_complete, this state would be reported and, on a
        confirmed 'y', would permanently delete the package-side original
        that still holds the files the interrupted copy never got to --
        the only remaining copy of them anywhere."""
        home = tmp_path / "home"
        repo = tmp_path / "repo"
        source = repo / "claude" / ".claude" / "plans"
        source.mkdir(parents=True)
        (source / "plan-a.md").write_text("# plan a\n")
        (source / "plan-b.md").write_text("# plan b -- never copied\n")
        target = home / ".claude" / "plans"
        target.mkdir(parents=True)
        (target / "plan-a.md").write_text("# plan a\n")
        # No _seed_completed_migration_backup call: no backup directory, no
        # completion sentinel -- exactly the interrupted-copy state.

        result = _run_report_and_clean(repo, "plans", home, stdin="y\n")

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert result.stdout == "" and result.stderr == "", (
            "an incomplete migration must not even be reported, let alone "
            f"offered for deletion; stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert source.is_dir(), "the package-side original must survive untouched"
        assert (source / "plan-b.md").read_text() == "# plan b -- never copied\n", (
            "the only remaining copy of the never-copied file must not be deleted"
        )
