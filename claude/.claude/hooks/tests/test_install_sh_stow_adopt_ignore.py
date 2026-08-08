"""Tests for install.sh's `stow --adopt --ignore=...` invocation against a
real `stow` binary. Pins the fix for a bug where the --ignore patterns were
anchored to each item's basename ('^briefs$') instead of its path relative
to the package root ('^\\.claude/briefs$') -- the basename form never
matches '.claude/briefs', so once '.claude' is unfolded (forced real by
install.sh's own `mkdir -p "$HOME/.claude"`), stow silently walked into the
supposedly-ignored directory and adopted every file inside individually.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_INSTALL_SH = Path(__file__).resolve().parents[4] / "install.sh"
_STOW = shutil.which("stow")

_FIXTURE_START = "# INSTALL_TEST_FIXTURE: stow-adopt-ignore — start\n"
_FIXTURE_END = "# INSTALL_TEST_FIXTURE: stow-adopt-ignore — end"


def _extract_stow_adopt_block() -> str:
    """Same marker-delimited extraction strategy as the other
    test_install_sh_*.py files -- syntax-matching the stow invocation would
    silently pick up an edited command line, or miss one, on reordering."""
    install_text = _INSTALL_SH.read_text()
    start = install_text.find(_FIXTURE_START)
    assert start != -1, f"{_FIXTURE_START!r} not found in {_INSTALL_SH}"
    end = install_text.find(_FIXTURE_END, start)
    assert end != -1, f"{_FIXTURE_END!r} not found after start marker in {_INSTALL_SH}"
    block = install_text[start + len(_FIXTURE_START) : end]
    assert "stow" in block and "--adopt" in block, (
        f"extracted block is missing the stow invocation; markers in {_INSTALL_SH} "
        f"are probably misplaced. Got: {block!r}"
    )
    return block


def _make_package(tmp_path: Path) -> Path:
    """A throwaway stow package mirroring this repo's real shape: a
    top-level 'claude' package directory containing '.claude/briefs' (an
    ignored name) alongside '.claude/skills' (an ordinary stowed item), so
    the test can confirm --ignore protects the former without disabling
    stow for the latter."""
    pkg_root = tmp_path / "pkg"
    briefs = pkg_root / "claude" / ".claude" / "briefs"
    briefs.mkdir(parents=True)
    (briefs / "existing.md").write_text("# existing brief, from the package\n")
    skills = pkg_root / "claude" / ".claude" / "skills" / "example"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("# example skill\n")
    return pkg_root


def _run_stow_adopt_block(pkg_root: Path, home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", "set -e\ncd \"$1\"\n" + _extract_stow_adopt_block(), "run_stow", str(pkg_root)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "HOME": str(home)},
    )


@pytest.mark.skipif(_STOW is None, reason="stow binary not on PATH")
class TestStowAdoptIgnorePattern:
    def test_pre_existing_file_in_an_ignored_nested_directory_is_left_untouched(
        self, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        pkg_root = _make_package(tmp_path)
        # install.sh's mkdir -p "$HOME/.claude" (run before this block) forces
        # .claude to unfold -- reproduce that precondition directly, plus the
        # real, pre-migrated file at the ignored name that stow_migrate_
        # adopted_dir would have produced upstream of this block.
        target_briefs = home / ".claude" / "briefs"
        target_briefs.mkdir(parents=True)
        (target_briefs / "existing.md").write_text("# existing brief, already migrated\n")

        result = _run_stow_adopt_block(pkg_root, home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        existing = target_briefs / "existing.md"
        assert existing.is_file() and not existing.is_symlink(), (
            "an --ignore'd nested directory's pre-existing file must not be "
            f"adopted by stow; stow output: {result.stderr!r}"
        )
        assert existing.read_text() == "# existing brief, already migrated\n"

    def test_an_unignored_item_still_gets_symlinked_normally(self, tmp_path: Path) -> None:
        """Sanity check that the --ignore patterns are scoped to exactly the
        3 names, not accidentally disabling stow's normal linking."""
        home = tmp_path / "home"
        pkg_root = _make_package(tmp_path)
        (home / ".claude").mkdir(parents=True)

        result = _run_stow_adopt_block(pkg_root, home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        skills_link = home / ".claude" / "skills"
        assert skills_link.is_symlink(), (
            f"an ordinary, non-ignored package item must still be symlinked; "
            f"stow output: {result.stderr!r}"
        )
