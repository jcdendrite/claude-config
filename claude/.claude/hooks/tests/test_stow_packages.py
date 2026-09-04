"""Tests pinning stow-packages.sh's manifest against its two consumers:

- every row must name a package directory that actually exists on disk
- every row the manifest prints must actually be stowed, not silently
  dropped by install.sh's loop

Applies the inventory-versus-consumer shape
test_install_sh_sentinel_inventory.py uses for SENTINEL_INVENTORY to the
stow-package manifest instead.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from helpers import REPO_ROOT, SCRIPTS_DIR

_INSTALL_SH = REPO_ROOT / "install.sh"
_STOW_PACKAGES_SH = SCRIPTS_DIR / "stow-packages.sh"
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
    return install_text[start + len(_FIXTURE_START) : end]


def _manifest_rows() -> list[tuple[str, str]]:
    result = subprocess.run(
        [str(_STOW_PACKAGES_SH)], capture_output=True, text=True, check=True
    )
    rows = []
    for line in result.stdout.splitlines():
        package_dir, _, stow_target = line.partition("\t")
        rows.append((package_dir, stow_target))
    return rows


class TestManifestRowsNameRealPackageDirectories:
    def test_every_row_names_a_directory_that_exists_on_disk(self) -> None:
        rows = _manifest_rows()
        assert rows, "stow-packages.sh printed no rows"
        for package_dir, _stow_target in rows:
            assert (REPO_ROOT / package_dir).is_dir(), (
                f"stow-packages.sh printed package directory {package_dir!r}, "
                f"which does not exist under {REPO_ROOT}"
            )


class TestRefusesWhenResolvedRootLacksAPackage:
    def test_refuses_when_resolved_repo_dir_is_missing_a_listed_package(
        self, tmp_path: Path
    ) -> None:
        """Deny-path counterpart to the row-existence check above, mirroring
        test_register_marketplace.py's TestSelfLocation shape: readlink -f
        fully canonicalizes a symlinked invocation, so the only way to
        exercise the legitimacy check's failure mode is a plain (non-symlink)
        copy of the script placed outside a real checkout, mimicking what a
        miscalculated self-location would produce. Self-location only counts
        directory levels, not names, so nesting the copy three levels below
        tmp_path (with no "claude" subdirectory anywhere under it) is enough
        to make tmp_path resolve as REPO_DIR with no "claude" package."""
        fake_scripts_dir = tmp_path / "some" / "nested" / "scripts"
        fake_scripts_dir.mkdir(parents=True)
        fake_script = fake_scripts_dir / "stow-packages.sh"
        fake_script.write_text(_STOW_PACKAGES_SH.read_text())
        fake_script.chmod(0o755)

        result = subprocess.run(
            [str(fake_script)], capture_output=True, text=True, check=False
        )

        assert result.returncode == 1
        assert result.stdout == ""
        assert "refusing to list a package that doesn't exist" in result.stderr


def _make_two_package_repo(tmp_path: Path) -> Path:
    """A throwaway 'claude' package mirroring this repo's real shape (enough
    content for stow_untracked_package_entries to run cleanly) plus a second,
    unrelated package -- this test's stub stow-packages.sh manifest lists
    both, so install.sh's loop must stow both or the second package's marker
    file is never symlinked.

    A real git repo, not just a directory tree: stow_untracked_package_entries
    tells adopted content apart from package content via `git ls-files`.
    """
    pkg_root = tmp_path / "pkg"
    skills = pkg_root / "claude" / ".claude" / "skills" / "example"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("# example skill\n")
    scripts_dir = pkg_root / "claude" / ".claude" / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "_stow_migration_lib.sh").symlink_to(
        SCRIPTS_DIR / "_stow_migration_lib.sh"
    )
    second_pkg = pkg_root / "second-pkg"
    second_pkg.mkdir(parents=True)
    (second_pkg / "marker.txt").write_text("second package content\n")
    # Shaped like one of stow_ignore_args' hardcoded --ignore patterns
    # (install.sh: --ignore='^\.claude/plans$'), which install.sh's loop
    # must attach only to the "claude" row -- if it attached to every row
    # instead, this entry would be silently skipped rather than symlinked.
    second_pkg_plans = second_pkg / ".claude" / "plans"
    second_pkg_plans.mkdir(parents=True)
    (second_pkg_plans / "example.md").write_text("# second package plan\n")

    fake_manifest = scripts_dir / "stow-packages.sh"
    fake_manifest.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'claude\\t.\\n'\n"
        "printf 'second-pkg\\t.\\n'\n"
    )
    fake_manifest.chmod(0o755)

    subprocess.run(["git", "init", "-q"], cwd=pkg_root, check=True)
    # `git add`, deliberately no `git commit` -- see the identical comment in
    # test_install_sh_stow_adopt_ignore.py's _make_package.
    subprocess.run(
        ["git", "add", "claude/.claude/skills", "claude/.claude/scripts", "second-pkg"],
        cwd=pkg_root,
        check=True,
    )
    return pkg_root


@pytest.mark.skipif(_STOW is None, reason="stow binary not on PATH")
class TestInstallShStowsEveryManifestRow:
    def test_a_second_manifest_row_is_stowed_not_only_the_first(
        self, tmp_path: Path
    ) -> None:
        """A regression test for a loop that silently degrades back to a
        hardcoded single `stow ... claude` line: with a stub manifest
        printing two rows, both packages' content must land under $HOME."""
        home = tmp_path / "home"
        # install.sh's own `mkdir -p "$HOME/.claude"` (run before this block,
        # not itself extracted) forces .claude to unfold before stow ever
        # runs -- reproduce that precondition directly, as the sibling
        # test_install_sh_stow_adopt_ignore.py tests also do.
        (home / ".claude").mkdir(parents=True)
        pkg_root = _make_two_package_repo(tmp_path)

        script = (
            f'. "{SCRIPTS_DIR / "_stow_migration_lib.sh"}"\n'
            "set -e\n"
            'cd "$1"\n' + _extract_stow_adopt_block()
        )
        result = subprocess.run(
            ["bash", "-c", script, "run_stow", str(pkg_root)],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "HOME": str(home), "REPO_DIR": str(pkg_root)},
        )

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert (home / ".claude" / "skills").is_symlink(), (
            "the first manifest row ('claude') must still be stowed; "
            f"stow output: {result.stderr!r}"
        )
        assert (home / "marker.txt").is_symlink(), (
            "the second manifest row ('second-pkg') must also be stowed -- "
            "a hardcoded loop covering only the first row would leave "
            f"$HOME/marker.txt missing; stow output: {result.stderr!r}"
        )

    def test_stow_ignore_args_are_not_attached_to_a_non_claude_row(
        self, tmp_path: Path
    ) -> None:
        """Regression test for a loop collapsed back to one unconditional
        `stow ... "$package_dir"` call for every row: that shape still
        passes every other test in this file, since none of them plant an
        --ignore-shaped entry under a non-'claude' package to notice
        stow_ignore_args leaking onto it. second-pkg's '.claude/plans' entry
        (set up in _make_two_package_repo, shaped like the hardcoded
        '^\\.claude/plans$' --ignore pattern) must still be symlinked
        normally -- install.sh's loop attaches stow_ignore_args only to the
        row named 'claude'."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        pkg_root = _make_two_package_repo(tmp_path)

        script = (
            f'. "{SCRIPTS_DIR / "_stow_migration_lib.sh"}"\n'
            "set -e\n"
            'cd "$1"\n' + _extract_stow_adopt_block()
        )
        result = subprocess.run(
            ["bash", "-c", script, "run_stow", str(pkg_root)],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "HOME": str(home), "REPO_DIR": str(pkg_root)},
        )

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert (home / ".claude" / "plans").is_symlink(), (
            "second-pkg's '.claude/plans' entry must be symlinked, not "
            "ignored -- stow_ignore_args belongs only to the 'claude' row; "
            f"stow output: {result.stderr!r}"
        )
