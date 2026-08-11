"""Pins pytest's collection config against two failure modes surfaced by a
stale linked worktree nested at claude/.claude/worktrees/<branch>: a full
repo checkout under the collection root breaks collection outright (it
carries evals/fixtures/temp-project/tests/test_calculator.py, which fails to
import), and .gitignore excluding claude/.claude/worktrees/ does nothing to
stop pytest from walking into it — gitignore does not scope collection.

Run with: pytest claude/.claude/
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from helpers import REPO_ROOT


class TestNestedWorktreeExcludedFromCollection:
    """Regression test for the stale claude/.claude/worktrees/handoff-scratch
    checkout that made `pytest claude/.claude/` abort collection entirely
    with `ModuleNotFoundError: No module named 'calculator'` from
    evals/fixtures/temp-project/tests/test_calculator.py."""

    def test_seeded_nested_worktree_contributes_no_collected_tests(self):
        # No -n0 here: pytest-xdist isn't a dependency yet, and this subprocess
        # invocation would fail with "unrecognized arguments: -n0". Once xdist
        # lands and addopts carries -n auto, add -n0 so this collect-only run
        # doesn't spawn workers of its own.
        #
        # pid-suffixed so concurrent `pytest claude/.claude/` invocations
        # against this checkout (routine in this repo's multi-worktree
        # workflow) don't race on the same leaf directory; self-cleaned
        # up front so a leftover from a prior interrupted run under the
        # same pid doesn't turn every subsequent run into a hard failure.
        fake_worktree = REPO_ROOT / "claude" / ".claude" / "worktrees" / f"pytest-collection-regression-fixture-{os.getpid()}"
        if fake_worktree.exists():
            shutil.rmtree(fake_worktree)
        fake_worktree_tests = fake_worktree / "sub" / "tests"
        fake_worktree_tests.mkdir(parents=True)
        (fake_worktree_tests / "test_should_never_collect.py").write_text(
            "import this_module_does_not_exist\n\n\ndef test_x():\n    assert True\n"
        )
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "claude/.claude/", "--collect-only", "-q", "--rootdir", str(REPO_ROOT)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=120,
            )
        finally:
            shutil.rmtree(fake_worktree)

        assert result.returncode == 0, (
            f"collection failed with the fake nested worktree present "
            f"(exit {result.returncode}):\n{result.stdout}\n{result.stderr}"
        )

        # Parse collected node ids rather than substring-matching raw stdout:
        # `-q --collect-only` prints one "path::test" line per collected
        # test plus a trailing summary line with no "::" in it, so filtering
        # on "::" isolates node ids from the summary before checking each
        # one's file-path component for a "worktrees" path segment.
        collected_paths_under_worktrees = [
            line
            for line in result.stdout.splitlines()
            if "::" in line and "worktrees" in Path(line.split("::", 1)[0]).parts
        ]
        assert not collected_paths_under_worktrees, (
            f"collected node id(s) reference a path under a worktrees/ directory — "
            f"norecursedirs is not excluding nested worktree checkouts: "
            f"{collected_paths_under_worktrees}"
        )


class TestNorecursedirsDefaultsPreserved:
    """pytest's norecursedirs ini option REPLACES the built-in default list
    rather than extending it — setting norecursedirs = ["worktrees"] alone
    silently drops ".*" and re-exposes hidden directories to collection.
    This pins the configured list as a superset of pytest's own defaults,
    reading the default from pytest itself rather than a second hardcoded
    copy that could drift from the installed version."""

    def test_configured_norecursedirs_is_superset_of_pytest_defaults(self):
        # No public pytest API exposes the built-in norecursedirs default as
        # of pytest 8.4.2 (checked dir(_pytest.main) and Parser's public
        # surface) — Parser._inidict is the only source, and it already
        # raises PytestDeprecationWarning for private-attribute access.
        # Accepted trade-off: switch to a public accessor if pytest ever
        # adds one.
        import _pytest.main as pytest_main
        from _pytest.config.argparsing import Parser

        parser = Parser()
        pytest_main.pytest_addoption(parser)
        pytest_defaults = set(parser._inidict["norecursedirs"][2])
        assert pytest_defaults, "could not introspect pytest's default norecursedirs — pytest internals moved"

        import tomllib

        with (REPO_ROOT / "pyproject.toml").open("rb") as f:
            config = tomllib.load(f)
        configured = set(config["tool"]["pytest"]["ini_options"]["norecursedirs"])

        missing = pytest_defaults - configured
        assert not missing, (
            f"pyproject.toml's norecursedirs dropped pytest default(s) {missing} — "
            f"norecursedirs replaces rather than extends the built-in list, so every "
            f"default must be restated alongside any repo-specific addition"
        )
        assert "worktrees" in configured, "norecursedirs no longer excludes nested worktree checkouts"
