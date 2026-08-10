"""Tests for install.sh's `stow --ignore=...` invocation against a real
`stow` binary. Pins two things: the --ignore patterns are anchored to each
item's path relative to the package root ('^\\.claude/briefs$'), not its
basename ('^briefs$') -- the basename form never matches '.claude/briefs',
so once '.claude' is unfolded (forced real by install.sh's own
`mkdir -p "$HOME/.claude"`), stow would silently walk into the
supposedly-ignored directory and adopt every file inside individually. And
that the invocation no longer passes --adopt at all -- the --ignore patterns
are derived from git tracking, not hardcoded, so they cover whatever a prior
`stow --adopt` run pulled in, not just the three names an earlier version of
this script special-cased.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from helpers import SCRIPTS_DIR

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
    assert "stow -v" in block, (
        f"extracted block is missing the stow invocation; markers in {_INSTALL_SH} "
        f"are probably misplaced. Got: {block!r}"
    )
    return block


def _make_package(tmp_path: Path) -> Path:
    """A throwaway stow package mirroring this repo's real shape: a
    top-level 'claude' package directory containing '.claude/briefs' (an
    untracked, previously-adopted name) alongside '.claude/skills' (an
    ordinary tracked, stowed item) and a real
    '.claude/scripts/_stow_migration_lib.sh' -- the extracted block
    re-sources this to reach stow_untracked_package_entries, so the fixture
    needs a working copy, not a stub.

    A real git repo, not just a directory tree: stow_untracked_package_entries
    tells adopted content apart from package content via `git ls-files`, not
    .gitignore, so the fixture must actually track 'skills' and
    'scripts' and leave 'briefs' untracked to exercise that distinction.
    """
    pkg_root = tmp_path / "pkg"
    briefs = pkg_root / "claude" / ".claude" / "briefs"
    briefs.mkdir(parents=True)
    (briefs / "existing.md").write_text("# existing brief, from the package\n")
    skills = pkg_root / "claude" / ".claude" / "skills" / "example"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("# example skill\n")
    scripts_dir = pkg_root / "claude" / ".claude" / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "_stow_migration_lib.sh").symlink_to(SCRIPTS_DIR / "_stow_migration_lib.sh")

    subprocess.run(["git", "init", "-q"], cwd=pkg_root, check=True)
    # `git add`, deliberately no `git commit`: `git ls-files` (default, no
    # flags) reads the index, not HEAD, so staged-but-uncommitted is already
    # "tracked" for stow_untracked_package_entries's purposes -- skipping
    # the commit avoids needing a throwaway user.name/user.email here.
    subprocess.run(
        ["git", "add", "claude/.claude/skills", "claude/.claude/scripts"],
        cwd=pkg_root,
        check=True,
    )
    return pkg_root


def _run_stow_adopt_block(pkg_root: Path, home: Path) -> subprocess.CompletedProcess:
    """The extracted block calls stow_untracked_package_entries, which
    install.sh's own earlier (unextracted) sourcing line provides in
    production -- source it here instead of repeating that line inside the
    marked block itself, which confuses shellcheck's forward-reference
    analysis for install.sh's other, unrelated calls to functions from the
    same library."""
    script = f'. "{SCRIPTS_DIR / "_stow_migration_lib.sh"}"\nset -e\ncd "$1"\n' + _extract_stow_adopt_block()
    return subprocess.run(
        ["bash", "-c", script, "run_stow", str(pkg_root)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "HOME": str(home), "REPO_DIR": str(pkg_root)},
    )


@pytest.mark.skipif(_STOW is None, reason="stow binary not on PATH")
class TestStowAdoptIgnorePattern:
    def test_stow_invocation_does_not_use_adopt(self, tmp_path: Path) -> None:
        """--adopt is gone, so a real non-symlink file at a *tracked*
        package path makes stow refuse the whole invocation instead of
        silently pulling that file into the package -- exercised
        indirectly by the other tests here, pinned directly by this one.
        Checked against the actual stow invocation line, not the whole
        block -- a surrounding comment is allowed to say why --adopt is
        gone."""
        stow_lines = [
            line for line in _extract_stow_adopt_block().splitlines()
            if line.strip().startswith("stow ")
        ]
        assert stow_lines, "no `stow ...` invocation line found in the extracted block"
        assert not any("--adopt" in line for line in stow_lines)

    def test_pre_existing_file_in_an_untracked_nested_directory_is_left_untouched(
        self, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        pkg_root = _make_package(tmp_path)
        # install.sh's mkdir -p "$HOME/.claude" (run before this block) forces
        # .claude to unfold -- reproduce that precondition directly, plus the
        # real, pre-migrated file at the untracked name that
        # stow_unadopt_entry (or the older stow_migrate_adopted_dir) would
        # have produced upstream of this block.
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
        """Sanity check that the derived --ignore patterns cover exactly the
        untracked names, not accidentally disabling stow's normal linking
        for a tracked package entry."""
        home = tmp_path / "home"
        pkg_root = _make_package(tmp_path)
        (home / ".claude").mkdir(parents=True)

        result = _run_stow_adopt_block(pkg_root, home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        skills_link = home / ".claude" / "skills"
        assert skills_link.is_symlink(), (
            f"an ordinary, tracked package item must still be symlinked; "
            f"stow output: {result.stderr!r}"
        )

    def test_dotted_untracked_name_is_ignored_without_over_matching_a_sibling(
        self, tmp_path: Path
    ) -> None:
        """.claude.json is the real-world motivating case for escaping dots
        in the --ignore regex (documented elsewhere in this diff) -- an
        unescaped dot in an anchored Perl regex matches any character, so an
        under-escaped pattern for '.claude.json' would also match a sibling
        name like '.claudexjson' that should be symlinked normally."""
        home = tmp_path / "home"
        pkg_root = _make_package(tmp_path)
        dotted = pkg_root / "claude" / ".claude" / ".claude.json"
        dotted.write_text('{"fixture": true}')
        sibling = pkg_root / "claude" / ".claude" / ".claudexjson"
        sibling.write_text("# tracked sibling differing only at the dot position\n")
        subprocess.run(
            ["git", "add", "claude/.claude/.claudexjson"], cwd=pkg_root, check=True
        )
        target_dotted = home / ".claude" / ".claude.json"
        target_dotted.parent.mkdir(parents=True)
        target_dotted.write_text('{"already migrated": true}')

        result = _run_stow_adopt_block(pkg_root, home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert target_dotted.is_file() and not target_dotted.is_symlink(), (
            "the untracked dotted name's pre-existing real file must not be "
            f"adopted by stow; stow output: {result.stderr!r}"
        )
        assert target_dotted.read_text() == '{"already migrated": true}'
        sibling_link = home / ".claude" / ".claudexjson"
        assert sibling_link.is_symlink(), (
            "a tracked sibling differing only at the escaped dot's position "
            f"must still be symlinked normally, not swept in by an "
            f"under-escaped pattern; stow output: {result.stderr!r}"
        )
