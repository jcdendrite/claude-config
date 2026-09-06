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
_SKILLS_MIGRATION_FIXTURE_START = "# INSTALL_TEST_FIXTURE: skills-package-migration — start\n"
_SKILLS_MIGRATION_FIXTURE_END = "# INSTALL_TEST_FIXTURE: skills-package-migration — end"


def _extract_marked_block(start_marker: str, end_marker: str) -> str:
    """Same marker-delimited extraction strategy as the other
    test_install_sh_*.py files -- syntax-matching a block would silently
    pick up an edited command line, or miss one, on reordering."""
    install_text = _INSTALL_SH.read_text()
    start = install_text.find(start_marker)
    assert start != -1, f"{start_marker!r} not found in {_INSTALL_SH}"
    end = install_text.find(end_marker, start)
    assert end != -1, f"{end_marker!r} not found after start marker in {_INSTALL_SH}"
    return install_text[start + len(start_marker) : end]


def _extract_stow_adopt_block() -> str:
    return _extract_marked_block(_FIXTURE_START, _FIXTURE_END)


def _extract_skills_migration_and_stow_adopt_block() -> str:
    """The skills-package-migration block followed by the stow-adopt-ignore
    block, in that order, extracted for a single subprocess run -- so
    skills_migration_blocks_adopt, set by the first block, is visible to
    the manifest loop in the second."""
    return (
        _extract_marked_block(_SKILLS_MIGRATION_FIXTURE_START, _SKILLS_MIGRATION_FIXTURE_END)
        + "\n"
        + _extract_stow_adopt_block()
    )


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


def _make_claude_and_claude_skills_repo(tmp_path: Path) -> Path:
    """A throwaway top-level 'claude' package (enough for
    stow_untracked_package_entries to run cleanly) plus a real
    'claude-skills' package shaped like this repo's own claude-skills/skills
    stow target -- named to match stow-packages.sh's real row names, since
    install.sh's claude-skills stow-skip check matches package_dir ==
    "claude-skills" by field name, not position.

    stow-packages.sh is symlinked in rather than stubbed: it self-locates
    via its own $0, so following the symlink resolves to the real repo
    checkout and prints its real manifest rows regardless of pkg_root's own
    layout -- same technique as test_install_sh_stow_adopt_ignore.py's
    _make_package.
    """
    pkg_root = tmp_path / "pkg"
    scripts_dir = pkg_root / "claude" / ".claude" / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "_stow_migration_lib.sh").symlink_to(
        SCRIPTS_DIR / "_stow_migration_lib.sh"
    )
    (scripts_dir / "stow-packages.sh").symlink_to(SCRIPTS_DIR / "stow-packages.sh")
    (pkg_root / "claude" / "marker.txt").write_text("claude package content\n")

    other_skill = pkg_root / "claude-skills" / "skills" / "other-skill"
    other_skill.mkdir(parents=True)
    (other_skill / "SKILL.md").write_text("# other skill\n")

    subprocess.run(["git", "init", "-q"], cwd=pkg_root, check=True)
    subprocess.run(
        ["git", "add", "claude/.claude/scripts", "claude/marker.txt", "claude-skills"],
        cwd=pkg_root,
        check=True,
    )
    return pkg_root


def _run_skills_migration_and_stow_adopt_block(
    pkg_root: Path, home: Path
) -> subprocess.CompletedProcess:
    script = (
        f'. "{SCRIPTS_DIR / "_stow_migration_lib.sh"}"\n'
        "set -e\n"
        'cd "$1"\n' + _extract_skills_migration_and_stow_adopt_block()
    )
    return subprocess.run(
        ["bash", "-c", script, "run_stow", str(pkg_root)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "HOME": str(home), "REPO_DIR": str(pkg_root)},
    )


@pytest.mark.skipif(_STOW is None, reason="stow binary not on PATH")
class TestSkillsMigrationBlocksClaudeSkillsStow:
    def test_real_directory_at_skills_target_skips_the_stow_call_not_merges_into_it(
        self, tmp_path: Path
    ) -> None:
        """Regression test for the claude-skills stow-skip check: a real,
        non-symlink ~/.claude/skills with pre-existing content must not be
        silently unfolded and merged into by stow (no --ignore args are
        attached to the claude-skills row) -- that would reintroduce the
        exact "ghost skill" duplication class GH-849 exists to fix. Runs the
        skills-package-migration block and the stow-adopt-ignore block
        together in one subprocess, so the flag the first block sets is
        visible to the second's manifest loop. The install must hard-fail
        (non-zero exit) rather than silently continue with claude-skills
        unstowed."""
        home = tmp_path / "home"
        skills_dir = home / ".claude" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "existing.md").write_text("# pre-existing, real content\n")
        pkg_root = _make_claude_and_claude_skills_repo(tmp_path)

        result = _run_skills_migration_and_stow_adopt_block(pkg_root, home)

        assert result.returncode != 0, f"stderr={result.stderr!r}"
        assert "skipping stow of claude-skills" in result.stderr
        assert "claude-skills was not stowed" in result.stderr
        assert skills_dir.is_dir() and not skills_dir.is_symlink()
        assert (skills_dir / "existing.md").read_text() == "# pre-existing, real content\n"
        assert not (skills_dir / "other-skill").exists(), (
            "the claude-skills package's own content must not be merged "
            f"into the real pre-existing directory; stderr={result.stderr!r}"
        )
        assert (home / "marker.txt").is_symlink(), (
            "the unrelated 'claude' row is stowed before the manifest loop "
            "reaches claude-skills, so it must still succeed even though "
            "the script hard-fails once the loop finishes"
        )

    def test_regular_file_at_skills_target_skips_the_stow_call_and_hard_fails(
        self, tmp_path: Path
    ) -> None:
        """Mirrors the real-directory case above for the "neither symlink
        nor directory" branch: a plain regular file at ~/.claude/skills must
        also skip the claude-skills stow and hard-fail the install, not
        merge or silently succeed with exit 0."""
        home = tmp_path / "home"
        skills_path = home / ".claude" / "skills"
        skills_path.parent.mkdir(parents=True)
        skills_path.write_text("not a directory, not a symlink\n")
        pkg_root = _make_claude_and_claude_skills_repo(tmp_path)

        result = _run_skills_migration_and_stow_adopt_block(pkg_root, home)

        assert result.returncode != 0, f"stderr={result.stderr!r}"
        assert "skipping stow of claude-skills" in result.stderr
        assert "claude-skills was not stowed" in result.stderr
        assert skills_path.is_file() and not skills_path.is_symlink()
        assert (home / "marker.txt").is_symlink(), (
            "the unrelated 'claude' row is stowed before the manifest loop "
            "reaches claude-skills, so it must still succeed even though "
            "the script hard-fails once the loop finishes"
        )

    def test_symlink_pointing_elsewhere_at_skills_target_skips_the_stow_call_and_hard_fails(
        self, tmp_path: Path
    ) -> None:
        """A ~/.claude/skills symlink resolving into neither the old nor the
        new skills location must also skip the claude-skills stow and
        hard-fail the install -- stow would otherwise refuse outright with
        no --ignore protecting this row, aborting every other package's
        stow call too."""
        home = tmp_path / "home"
        home_claude_dir = home / ".claude"
        home_claude_dir.mkdir(parents=True)
        pkg_root = _make_claude_and_claude_skills_repo(tmp_path)
        unrelated_target = tmp_path / "somewhere-else" / "not-skills-at-all"
        unrelated_target.mkdir(parents=True)
        skills_link = home_claude_dir / "skills"
        skills_link.symlink_to(unrelated_target)

        result = _run_skills_migration_and_stow_adopt_block(pkg_root, home)

        assert result.returncode != 0, f"stderr={result.stderr!r}"
        assert "skipping stow of claude-skills" in result.stderr
        assert "claude-skills was not stowed" in result.stderr
        assert skills_link.is_symlink() and skills_link.resolve() == unrelated_target.resolve(), (
            "the unrelated symlink must be left exactly alone"
        )
        assert (home / "marker.txt").is_symlink(), (
            "the unrelated 'claude' row is stowed before the manifest loop "
            "reaches claude-skills, so it must still succeed even though "
            "the script hard-fails once the loop finishes"
        )

    def test_orphaned_content_at_old_package_path_does_not_block_the_stow(
        self, tmp_path: Path
    ) -> None:
        """A stale old-path symlink that removes cleanly, with real content
        left behind at claude/.claude/skills (e.g. an untracked leftover
        never migrated by a `git mv`), must not block claude-skills' own
        stow -- skills_migration_blocks_adopt is set only for content
        genuinely sitting at ~/.claude/skills, not leftovers at the
        superseded package-side path."""
        home = tmp_path / "home"
        home_claude_dir = home / ".claude"
        home_claude_dir.mkdir(parents=True)
        pkg_root = _make_claude_and_claude_skills_repo(tmp_path)

        orphaned_dir = pkg_root / "claude" / ".claude" / "skills"
        orphaned_dir.mkdir(parents=True)
        (orphaned_dir / "leftover.md").write_text("# orphaned content\n")
        (home_claude_dir / "skills").symlink_to(orphaned_dir)

        result = _run_skills_migration_and_stow_adopt_block(pkg_root, home)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert "removed" in result.stderr and "stale symlink" in result.stderr
        assert "still holds real content" in result.stderr
        skills_dir = home_claude_dir / "skills"
        assert skills_dir.is_symlink()
        assert (skills_dir / "other-skill").exists(), (
            "claude-skills' own content must land under ~/.claude/skills "
            f"when nothing blocks the stow; stderr={result.stderr!r}"
        )

    # The rm-failure sub-case has no combined-block test alongside the three
    # above: chmod'ing ~/.claude unwritable to force the rm failure also
    # blocks the unrelated "claude" package's own stow call, since its
    # package tree includes a .claude/scripts subtree that must be created
    # under the same now-read-only directory. See
    # test_install_sh_skills_migration.py's
    # test_rm_failure_sets_skills_migration_blocks_adopt for that branch's
    # flag-observation coverage instead.


def _make_repo_with_stub_manifest(tmp_path: Path, manifest_script: str) -> Path:
    """A minimal package tree with `manifest_script` standing in for
    stow-packages.sh. These deny-path tests exercise the manifest loop's
    own error handling before any `stow` invocation is reached, so no real
    stow-packages.sh or claude-skills package content is needed."""
    pkg_root = tmp_path / "pkg"
    scripts_dir = pkg_root / "claude" / ".claude" / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "_stow_migration_lib.sh").symlink_to(
        SCRIPTS_DIR / "_stow_migration_lib.sh"
    )
    fake_manifest = scripts_dir / "stow-packages.sh"
    fake_manifest.write_text(manifest_script)
    fake_manifest.chmod(0o755)

    subprocess.run(["git", "init", "-q"], cwd=pkg_root, check=True)
    subprocess.run(["git", "add", "claude/.claude/scripts"], cwd=pkg_root, check=True)
    return pkg_root


def _run_stow_adopt_block_only(pkg_root: Path, home: Path) -> subprocess.CompletedProcess:
    script = (
        f'. "{SCRIPTS_DIR / "_stow_migration_lib.sh"}"\n'
        "set -e\n"
        'cd "$1"\n' + _extract_stow_adopt_block()
    )
    return subprocess.run(
        ["bash", "-c", script, "run_stow", str(pkg_root)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "HOME": str(home), "REPO_DIR": str(pkg_root)},
    )


class TestManifestEnumerationDenyPaths:
    def test_enumeration_failure_exits_nonzero_and_attempts_no_stow(
        self, tmp_path: Path
    ) -> None:
        """A stow-packages.sh that fails outright must abort the install
        with a clear message, not fall through and attempt to stow an
        empty or partial manifest."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        pkg_root = _make_repo_with_stub_manifest(tmp_path, "#!/usr/bin/env bash\nexit 1\n")

        result = _run_stow_adopt_block_only(pkg_root, home)

        assert result.returncode != 0
        assert "could not enumerate stow packages" in result.stderr
        assert not any((home / ".claude").iterdir()), (
            "no package should have been stowed into $HOME/.claude when "
            "enumeration failed before the manifest loop ever ran"
        )

    def test_manifest_missing_claude_row_exits_nonzero(self, tmp_path: Path) -> None:
        """A manifest that never prints a row named 'claude' -- the
        mandatory package -- must abort rather than silently continue
        having stowed nothing essential."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        pkg_root = _make_repo_with_stub_manifest(tmp_path, "#!/usr/bin/env bash\n")

        result = _run_stow_adopt_block_only(pkg_root, home)

        assert result.returncode != 0
        assert "manifest has no row named 'claude'" in result.stderr


def _bin_dir_with_readlink_returning_garbage_on_failure(tmp_path: Path) -> Path:
    """A PATH directory mirroring real system binaries, but with `readlink`
    replaced by a stub mimicking BSD readlink -f's dangling-target bug: it
    prints a partial/garbage path to stdout AND exits non-zero, unlike GNU
    readlink, which prints nothing on failure. Same PATH-stub technique as
    test_install_sh_local_bin_path.py's _bin_dir_with_failing_syntax_check."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "readlink"
    stub.write_text('#!/bin/sh\nprintf "%s" "/garbage-partial-path"\nexit 1\n')
    stub.chmod(0o755)
    for real_bin_dir in (Path("/bin"), Path("/usr/bin")):
        if not real_bin_dir.is_dir():
            continue
        for entry in real_bin_dir.iterdir():
            if entry.name == "readlink":
                continue
            dest = bin_dir / entry.name
            if not dest.exists():
                dest.symlink_to(entry)
    return bin_dir


class TestSelfLocationSurvivesReadlinkCorruption:
    def test_dangling_readlink_garbage_does_not_corrupt_repo_dir(
        self, tmp_path: Path
    ) -> None:
        """A readlink -f that fails but still writes partial garbage to
        stdout (the documented BSD dangling-symlink bug) must not corrupt
        REPO_DIR via string concatenation. Locks in the two-step
        exit-status-checked fix over the old `cmd1 || cmd2`-inside-`$()`
        form, which would have fed
        "/garbage-partial-path" + "$0" (concatenated, no separator) into
        dirname, producing a REPO_DIR that resolves to nothing real."""
        bin_dir = _bin_dir_with_readlink_returning_garbage_on_failure(tmp_path)

        result = subprocess.run(
            [str(_STOW_PACKAGES_SH)],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "PATH": str(bin_dir)},
        )

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        rows = dict(line.split("\t") for line in result.stdout.splitlines())
        assert rows.get("claude") == ".", f"stdout={result.stdout!r}"
        assert rows.get("claude-skills") == ".claude", f"stdout={result.stdout!r}"
