"""Tests for install.sh's skills-package-migration block -- removes a stale
~/.claude/skills symlink still resolving into the pre-move
claude/.claude/skills location, so the stow-adopt-ignore block that runs
right after it can lay down the new claude-skills-package symlink at the
same target instead of stow refusing over a pre-existing conflicting link.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from helpers import SCRIPTS_DIR

_INSTALL_SH = Path(__file__).resolve().parents[4] / "install.sh"
_BASH = shutil.which("bash") or "/bin/bash"

_FIXTURE_START = "# INSTALL_TEST_FIXTURE: skills-package-migration — start\n"
_FIXTURE_END = "# INSTALL_TEST_FIXTURE: skills-package-migration — end"


def _extract_skills_migration_block() -> str:
    """Same marker-delimited extraction strategy as the other
    test_install_sh_*.py files -- syntax-matching the block would silently
    pick up an edited condition, or miss one, on reordering."""
    install_text = _INSTALL_SH.read_text()
    start = install_text.find(_FIXTURE_START)
    assert start != -1, f"{_FIXTURE_START!r} not found in {_INSTALL_SH}"
    end = install_text.find(_FIXTURE_END, start)
    assert end != -1, f"{_FIXTURE_END!r} not found after start marker in {_INSTALL_SH}"
    block = install_text[start + len(_FIXTURE_START) : end]
    assert "_stow_migration_lib_realpath_resolves_to" in block, (
        f"extracted block is missing the migration predicate; markers in {_INSTALL_SH} "
        f"are probably misplaced. Got: {block!r}"
    )
    return block


def _run_skills_migration(test_home: Path, repo_dir: Path) -> subprocess.CompletedProcess:
    """Run the extracted block with $HOME and $REPO_DIR pointed at isolated
    fixtures. No $CLAUDE_SESSION_MAY_BE_ACTIVE stub needed -- unlike the
    plans/handoffs/briefs migration and the un-adopt loop, this block never
    gates on it."""
    env = dict(os.environ)
    env["HOME"] = str(test_home)
    env["REPO_DIR"] = str(repo_dir)
    script = (
        f'. "{SCRIPTS_DIR / "_stow_migration_lib.sh"}"\n'
        "set -e\n"
        + _extract_skills_migration_block()
    )
    return subprocess.run(
        [_BASH, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


class TestInstallShSkillsMigration:
    def test_symlink_into_old_path_is_removed(self, tmp_path: Path) -> None:
        """The folded symlink every existing install carries: a dangling
        link into claude/.claude/skills, which no longer exists on disk
        now that the skills tree lives at claude-skills/skills but still
        resolves symbolically via realpath's non-strict, no-existence-
        required semantics."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        repo = tmp_path / "repo"
        old_target = repo / "claude" / ".claude" / "skills"
        (home / ".claude" / "skills").symlink_to(old_target)

        result = _run_skills_migration(home, repo)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert not (home / ".claude" / "skills").is_symlink(), (
            f"the stale symlink into the old path must be removed; stderr={result.stderr!r}"
        )
        assert "removed" in result.stderr

    def test_symlink_into_new_path_is_left_alone_and_idempotent_on_a_second_run(
        self, tmp_path: Path
    ) -> None:
        """A symlink already resolving into claude-skills/skills (this
        block's own prior run, followed by install.sh's later stow call)
        must survive unchanged -- and running the block again must not
        remove it either, since it no longer resolves into the old path."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        repo = tmp_path / "repo"
        new_target = repo / "claude-skills" / "skills"
        (new_target / "example").mkdir(parents=True)
        (new_target / "example" / "SKILL.md").write_text("# example skill\n")
        link = home / ".claude" / "skills"
        link.symlink_to(new_target)

        first = _run_skills_migration(home, repo)
        assert first.returncode == 0, f"stderr={first.stderr!r}"
        assert link.is_symlink() and link.resolve() == new_target.resolve()
        assert "already resolves to claude-skills/skills" in first.stderr
        assert "pointing somewhere other than" not in first.stderr
        assert "if it's stale" not in first.stderr

        second = _run_skills_migration(home, repo)
        assert second.returncode == 0, f"stderr={second.stderr!r}"
        assert link.is_symlink() and link.resolve() == new_target.resolve(), (
            "a second run must be a no-op on an already-migrated symlink"
        )
        assert "already resolves to claude-skills/skills" in second.stderr
        assert "pointing somewhere other than" not in second.stderr
        assert "if it's stale" not in second.stderr

    def test_real_directory_is_left_intact_and_reported(self, tmp_path: Path) -> None:
        """A real (non-symlink) ~/.claude/skills -- an install that adopted
        it, or a hand-created directory -- must never be touched; the
        message must name it as needing manual attention."""
        home = tmp_path / "home"
        skills_dir = home / ".claude" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "example.md").write_text("# not a symlink\n")
        repo = tmp_path / "repo"

        result = _run_skills_migration(home, repo)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert skills_dir.is_dir() and not skills_dir.is_symlink()
        assert (skills_dir / "example.md").read_text() == "# not a symlink\n"
        assert "real directory" in result.stderr

    def test_absent_is_a_noop(self, tmp_path: Path) -> None:
        """No ~/.claude/skills at all -- a fresh install, or one that never
        ran an earlier version of this repo -- must not error."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        repo = tmp_path / "repo"

        result = _run_skills_migration(home, repo)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert not (home / ".claude" / "skills").exists()

    def test_symlink_into_an_unrelated_third_path_is_left_intact(
        self, tmp_path: Path
    ) -> None:
        """The destructive-path branch: a symlink resolving into neither the
        old nor the new location -- some unrelated directory the user
        happens to have linked there -- must be left exactly alone. A future
        loosening of the realpath comparison could start deleting a user's
        own unrelated symlink here with nothing else to catch it."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        repo = tmp_path / "repo"
        unrelated_target = tmp_path / "somewhere-else" / "not-skills-at-all"
        unrelated_target.mkdir(parents=True)
        link = home / ".claude" / "skills"
        link.symlink_to(unrelated_target)

        result = _run_skills_migration(home, repo)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert link.is_symlink() and link.resolve() == unrelated_target.resolve(), (
            "an unrelated symlink must survive untouched"
        )
        assert "pointing somewhere other than" in result.stderr
