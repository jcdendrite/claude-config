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
    # The extracted block shells out to "$REPO_DIR/claude/.claude/scripts/
    # stow-packages.sh" -- symlinked in the same way as _stow_migration_lib.sh
    # above, rather than duplicated: stow-packages.sh self-locates via its own
    # $0, so following this symlink resolves to the real repo's checkout and
    # prints its real package list regardless of pkg_root's own layout.
    (scripts_dir / "stow-packages.sh").symlink_to(SCRIPTS_DIR / "stow-packages.sh")

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
        """A source-scan tripwire, not behavioral proof: this only confirms
        the wiring (no --adopt flag on the actual invocation line, as
        opposed to a surrounding comment merely saying why it's gone) --
        it would still pass on dead code. The behavioral proof that dropping
        --adopt actually changes what stow does with a real, pre-existing
        entry lives in
        test_pre_existing_file_in_an_untracked_nested_directory_is_left_untouched
        below, which runs the real `stow` binary and asserts the entry is
        left alone rather than pulled into the package."""
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


def _run_ignore_arg_construction_only(pkg_root: Path, home: Path, *, stub: str) -> subprocess.CompletedProcess:
    """Runs the real --ignore-arg-construction loop from the extracted
    block, replacing everything from the manifest loop onward with a printf
    of the constructed array. Isolates the loop's own set -e/continue
    behavior from real stow's separate, unrelated all-or-nothing conflict
    handling: a failure on one --ignore'd name makes real stow refuse the
    *entire* invocation, which would make "did the loop still process the
    other names" unobservable through stow's own exit code. Splits on the
    manifest loop's `while IFS=$'\\t' read ...` line rather than a comment,
    since that line is syntactically unique within the block and so survives
    a comment rewrap that would move a prose-anchored split point."""
    block = _extract_stow_adopt_block()
    stow_line_start = block.index("while IFS=$'\\t' read -r package_dir stow_target_rel")
    loop_only = block[:stow_line_start]
    script = (
        f'. "{SCRIPTS_DIR / "_stow_migration_lib.sh"}"\n'
        "set -e\n"
        f'cd "$1"\n'
        + stub
        + loop_only
        + 'printf \'%s\\n\' "${stow_ignore_args[@]}"\n'
    )
    return subprocess.run(
        ["bash", "-c", script, "run_ignore_construction", str(pkg_root)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "HOME": str(home), "REPO_DIR": str(pkg_root)},
    )


class TestIgnoreArgConstructionRegexEscapeFailure:
    """install.sh's --ignore-arg loop wraps _stow_migration_lib_regex_escape
    in `if ! escaped_name="$(...)"; then ... continue; fi` specifically so a
    failure there degrades gracefully instead of hard-aborting the whole
    script under `set -e` (a prior /code-review finding) -- pins that fix by
    stubbing the escape function to fail for one of several untracked
    names."""

    def test_escape_failure_for_one_name_does_not_abort_the_loop(
        self, tmp_path: Path
    ) -> None:
        """Uses "projects"/"sessions" rather than the fixture's default
        "briefs": plans/handoffs/briefs are excluded from
        stow_untracked_package_entries's output and get their --ignore args
        seeded explicitly (a separate code path) -- they never reach this
        loop's own _stow_migration_lib_regex_escape call at all, so stubbing
        a failure on "briefs" would test nothing about this loop."""
        home = tmp_path / "home"
        pkg_root = _make_package(tmp_path)
        failing = pkg_root / "claude" / ".claude" / "projects"
        failing.mkdir(parents=True)
        (failing / "session.json").write_text("{}")
        surviving = pkg_root / "claude" / ".claude" / "sessions"
        surviving.mkdir(parents=True)
        (surviving / "s.json").write_text("{}")

        stub = (
            "_stow_migration_lib_regex_escape() {\n"
            '  if [ "$1" = "projects" ]; then return 1; fi\n'
            "  python3 -c 'import re, sys\n"
            "print(re.escape(sys.argv[1]))' \"$1\"\n"
            "}\n"
        )

        result = _run_ignore_arg_construction_only(pkg_root, home, stub=stub)

        assert result.returncode == 0, (
            f"a single name's escape failure must not abort the whole "
            f"script under set -e; stderr={result.stderr!r}"
        )
        assert "could not regex-escape 'projects'" in result.stderr, (
            f"the specific per-name warning must be emitted; stderr={result.stderr!r}"
        )
        ignore_args = result.stdout.splitlines()
        assert "--ignore=^\\.claude/sessions$" in ignore_args, (
            f"the other untracked name ('sessions') must still get its "
            f"--ignore arg despite 'projects' failing to escape; got {ignore_args}"
        )
        assert "--ignore=^\\.claude/projects$" not in ignore_args, (
            "the failed name must be skipped entirely, not given a "
            f"fallback --ignore arg; got {ignore_args}"
        )
