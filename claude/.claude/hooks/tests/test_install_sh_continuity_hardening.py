"""Tests for the ~/.claude / ~/.claude.json permission-hardening step in install.sh."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from helpers import SCRIPTS_DIR

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


_MIGRATION_FIXTURE_START = "# INSTALL_TEST_FIXTURE: stow-adoption-migration — start\n"
_MIGRATION_FIXTURE_END = "# INSTALL_TEST_FIXTURE: stow-adoption-migration — end"


def _extract_migration_block() -> str:
    """Return the plans/handoffs/briefs stow-adoption migration block from
    install.sh — same delimited-extraction strategy as _extract_hardening_block
    above, for the same reason (avoid stubbing install.sh's other runtime
    dependencies just to reach this block)."""
    install_text = _INSTALL_SH.read_text()
    start = install_text.find(_MIGRATION_FIXTURE_START)
    assert start != -1, f"{_MIGRATION_FIXTURE_START!r} not found in {_INSTALL_SH}"
    end = install_text.find(_MIGRATION_FIXTURE_END, start)
    assert end != -1, f"{_MIGRATION_FIXTURE_END!r} not found after start marker in {_INSTALL_SH}"
    block = install_text[start + len(_MIGRATION_FIXTURE_START) : end]
    assert "stow_migrate_adopted_dir" in block, (
        f"extracted block is missing the migration call; markers in {_INSTALL_SH} are "
        f"probably misplaced. Got: {block!r}"
    )
    return block


def _run_migration_block(test_home: Path, repo_dir: Path) -> subprocess.CompletedProcess:
    """Run the extracted migration block with $HOME and $REPO_DIR pointed at
    isolated fixtures — REPO_DIR is normally computed earlier in install.sh
    (outside the extracted block), so the test supplies it directly.

    _claude_session_is_active_now is normally defined in install.sh's
    separate session-concurrency-check block (not extracted here) and calls
    the real `pgrep` -- on a machine actually running Claude Code (this test
    suite's own subprocess included), that would find a real session and
    make every fixture below skip its migration. Stub it to "no session"
    (return 1) so this block's own migration logic is what's under test, not
    this machine's process list; test_install_sh_session_concurrency_check.py
    covers the real function's own pgrep-driven behavior directly."""
    env = dict(os.environ)
    env["HOME"] = str(test_home)
    env["REPO_DIR"] = str(repo_dir)
    stub = "_claude_session_is_active_now() { return 1; }\n"
    return subprocess.run(
        [_BASH, "-c", "set -e\n" + stub + _extract_migration_block()],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _build_fake_repo(tmp_path: Path) -> Path:
    """Build a throwaway checkout at tmp_path/repo whose
    claude/.claude/scripts/_stow_migration_lib.sh is a symlink to the real
    file — exercises the actual migration logic under test, not a copy that
    could silently drift from it."""
    repo = tmp_path / "repo"
    scripts_dir = repo / "claude" / ".claude" / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "_stow_migration_lib.sh").symlink_to(SCRIPTS_DIR / "_stow_migration_lib.sh")
    return repo


def _adopt(repo: Path, home: Path, name: str, files: dict[str, str]) -> Path:
    """Create repo/claude/.claude/<name> with `files` and symlink
    home/.claude/<name> to it — mirrors what a stow --adopt run leaves
    behind for a name this migration targets. Returns the repo-side source
    directory."""
    source = repo / "claude" / ".claude" / name
    source.mkdir(parents=True)
    for rel_path, content in files.items():
        (source / rel_path).write_text(content)
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    # Absolute, unlike the relative symlinks a real `stow --adopt` produces --
    # inert here since _stow_migration_lib_symlink_resolves_to canonicalizes
    # both sides before comparing; accepted simplification, not a gap.
    (home / ".claude" / name).symlink_to(source)
    return source


class TestInstallShStowAdoptionMigration:
    """Tests for the plans/handoffs/briefs stow-adoption migration block —
    the static 3-name migrate-then-ignore sequence that replaces the old
    bare `stow --adopt`."""

    def test_symlinked_names_migrate_to_real_directories_with_identical_content(
        self, tmp_path: Path
    ) -> None:
        """Each of the 3 static names, when currently a symlink resolving
        into the checkout, becomes a plain real directory with
        byte-identical content."""
        home = tmp_path / "home"
        home.mkdir()
        repo = _build_fake_repo(tmp_path)
        sources = {
            name: _adopt(repo, home, name, {f"{name}.md": f"# {name} fixture\n"})
            for name in ("plans", "handoffs", "briefs")
        }

        result = _run_migration_block(home, repo)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        for name, source in sources.items():
            target = home / ".claude" / name
            assert target.is_dir() and not target.is_symlink(), (
                f"~/.claude/{name} must be a plain real directory after migration"
            )
            assert (target / f"{name}.md").read_text() == (source / f"{name}.md").read_text(), (
                f"~/.claude/{name} content must be byte-identical to the pre-migration source"
            )
        backup_root = home / ".claude-config-relocate-backup"
        assert oct(backup_root.stat().st_mode)[-3:] == "700", (
            f"{backup_root} lists backup directory names for every migrated "
            "entry and must itself be mode 700, not only its leaf backup dirs"
        )
        for backup_dir in backup_root.iterdir():
            assert oct(backup_dir.stat().st_mode)[-3:] == "700", (
                f"{backup_dir} holds plan/handoff/brief content and must be "
                "mode 700, matching install.sh's own chmod 700 on ~/.claude"
            )

    def test_resumes_from_intact_backup_when_target_is_missing(self, tmp_path: Path) -> None:
        """A prior run can fail after unlinking the symlink but before
        restoring the plain directory, leaving ~/.claude/<name> absent
        entirely with its backup intact. A later run must detect this as
        resumable and complete step (c) from that backup — not treat a
        missing target as "nothing to migrate" — and must not create a
        second backup, since step (a) is skipped on the resumed path."""
        home = tmp_path / "home"
        # install.sh runs `mkdir -p "$HOME/.claude"` before the migration
        # block (line 29) -- reproduce that precondition so this fixture
        # matches the state the block actually runs against.
        (home / ".claude").mkdir(parents=True)
        repo = _build_fake_repo(tmp_path)
        backup_dir = home / ".claude-config-relocate-backup" / "plans.20260101000000"
        backup_dir.mkdir(parents=True)
        (backup_dir / "p.md").write_text("# plans from backup\n")

        result = _run_migration_block(home, repo)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        target = home / ".claude" / "plans"
        assert target.is_dir() and not target.is_symlink()
        assert (target / "p.md").read_text() == "# plans from backup\n"
        backups = list((home / ".claude-config-relocate-backup").glob("plans.*"))
        assert backups == [backup_dir], (
            f"resuming must not create a second backup; got {backups}"
        )

    def test_resumes_from_intact_backup_when_target_is_a_dangling_symlink(
        self, tmp_path: Path
    ) -> None:
        """A prior run can fail after unlinking the old symlink and creating
        a new one pointing nowhere (or a race leaves a dangling symlink some
        other way), with its backup intact. A later run must detect this as
        resumable, remove the dangling symlink, and complete step (c) from
        that backup -- exercising step (b)'s `[ -L "$target" ] && rm -f`
        branch, which a merely-absent target never reaches."""
        home = tmp_path / "home"
        # install.sh runs `mkdir -p "$HOME/.claude"` before the migration
        # block (line 29) -- reproduce that precondition so this fixture
        # matches the state the block actually runs against.
        (home / ".claude").mkdir(parents=True)
        repo = _build_fake_repo(tmp_path)
        backup_dir = home / ".claude-config-relocate-backup" / "plans.20260101000000"
        backup_dir.mkdir(parents=True)
        (backup_dir / "p.md").write_text("# plans from backup\n")
        target = home / ".claude" / "plans"
        target.symlink_to(tmp_path / "nonexistent-source")

        result = _run_migration_block(home, repo)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert target.is_dir() and not target.is_symlink(), (
            "the dangling symlink must be replaced by a plain real directory"
        )
        assert (target / "p.md").read_text() == "# plans from backup\n"
        backups = list((home / ".claude-config-relocate-backup").glob("plans.*"))
        assert backups == [backup_dir], (
            f"resuming must not create a second backup; got {backups}"
        )

    def test_second_run_is_a_noop(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        repo = _build_fake_repo(tmp_path)
        _adopt(repo, home, "plans", {"p.md": "# plans\n"})

        first = _run_migration_block(home, repo)
        assert first.returncode == 0, first.stderr
        content_after_first = (home / ".claude" / "plans" / "p.md").read_text()

        second = _run_migration_block(home, repo)

        assert second.returncode == 0, second.stderr
        target = home / ".claude" / "plans"
        assert target.is_dir() and not target.is_symlink()
        assert (target / "p.md").read_text() == content_after_first
        backups = list((home / ".claude-config-relocate-backup").glob("plans.*"))
        assert len(backups) == 1, (
            f"a no-op second run must not create a new backup; got {backups}"
        )

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_per_entry_failure_is_caught_and_does_not_abort_the_others(
        self, tmp_path: Path
    ) -> None:
        """A failure migrating one name (plans) is reported in the summary
        without aborting migration of the other two (handoffs, briefs)."""
        home = tmp_path / "home"
        home.mkdir()
        repo = _build_fake_repo(tmp_path)
        _adopt(repo, home, "plans", {"p.md": "# plans\n"})
        _adopt(repo, home, "handoffs", {"h.md": "# handoffs\n"})
        _adopt(repo, home, "briefs", {"b.md": "# briefs\n"})

        # Execute-only, no read: the symlink still resolves (cd -P succeeds),
        # but cp -R cannot list plans' directory entries to back it up.
        plans_source = repo / "claude" / ".claude" / "plans"
        plans_source.chmod(0o100)
        try:
            result = _run_migration_block(home, repo)
        finally:
            plans_source.chmod(0o755)

        assert result.returncode == 0, (
            f"the wrapper must not abort on a per-entry failure; stderr={result.stderr!r}"
        )
        assert "plans" in result.stderr, (
            f"the failure summary must name the failed entry; stderr={result.stderr!r}"
        )
        assert (home / ".claude" / "plans").is_symlink(), (
            "a failed migration must leave the original symlink in place"
        )
        for name in ("handoffs", "briefs"):
            target = home / ".claude" / name
            assert target.is_dir() and not target.is_symlink(), (
                f"~/.claude/{name} must still migrate despite plans' failure"
            )

    def test_repairs_pre_existing_per_entry_symlinks_left_by_the_ignore_bug(
        self, tmp_path: Path
    ) -> None:
        """Reproduces the actual state found on a machine that already ran
        the pre-fix install.sh: $HOME/.claude/briefs is a real, top-level
        directory (so stow_migrate_adopted_dir's own idempotency check is a
        no-op), but the file inside it is a per-entry symlink into the repo
        -- left by the ineffective '^briefs$' --ignore pattern. The
        migration block's added stow_repair_nested_adoption call must fix
        this on a plain re-run, without a fresh top-level migration."""
        home = tmp_path / "home"
        repo = _build_fake_repo(tmp_path)
        source = repo / "claude" / ".claude" / "briefs"
        source.mkdir(parents=True)
        (source / "task-a.md").write_text("# task a\n")

        target = home / ".claude" / "briefs"
        target.mkdir(parents=True)
        (target / "task-a.md").symlink_to(source / "task-a.md")

        result = _run_migration_block(home, repo)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert target.is_dir() and not target.is_symlink()
        repaired = target / "task-a.md"
        assert repaired.is_file() and not repaired.is_symlink(), (
            "the per-entry symlink must be replaced by a real file"
        )
        assert repaired.read_text() == "# task a\n"
