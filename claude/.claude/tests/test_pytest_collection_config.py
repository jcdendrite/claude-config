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
        # No -n0 here: xdist's own pytest_configure() unconditionally skips
        # itself on --collect-only regardless of -n, so this collect-only
        # run was never going to spawn workers even with -n auto in addopts.
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


class TestPythonpathIncludesScriptsDir:
    """Pins that claude/.claude/scripts is on pytest's pythonpath, so
    `from transcript_analysis.corpus import ...`-style imports resolve
    without each test file inserting the directory itself."""

    def test_pythonpath_contains_scripts_dir(self):
        import tomllib

        with (REPO_ROOT / "pyproject.toml").open("rb") as f:
            config = tomllib.load(f)
        pythonpath = config["tool"]["pytest"]["ini_options"]["pythonpath"]

        assert "claude/.claude/scripts" in pythonpath, (
            "pythonpath dropped claude/.claude/scripts — the transcript_analysis "
            "package (and any test importing it directly) would no longer resolve"
        )


class TestAddoptsConfiguresXdistAndStrictMarkers:
    """Pins that pyproject.toml's addopts actually carries -n auto and
    --strict-markers, and that --strict-markers really enforces what it's
    there for — a typo'd marker name failing collection."""

    def test_addopts_contains_xdist_auto_and_strict_markers(self):
        import tomllib

        with (REPO_ROOT / "pyproject.toml").open("rb") as f:
            config = tomllib.load(f)
        addopts = config["tool"]["pytest"]["ini_options"]["addopts"]

        assert "-n" in addopts, "addopts dropped -n — pytest-xdist parallelization is no longer configured"
        assert "auto" in addopts, "addopts dropped auto — -n is no longer paired with an auto-detected worker count"
        assert "--strict-markers" in addopts, (
            "addopts dropped --strict-markers — a typo'd marker name would silently "
            "collect unfiltered instead of failing collection"
        )

    def test_strict_markers_rejects_unregistered_marker(self, tmp_path: Path):
        bogus_marker_test = tmp_path / "test_bogus_marker.py"
        bogus_marker_test.write_text(
            "import pytest\n\n\n"
            "@pytest.mark.definitely_not_a_real_marker\n"
            "def test_x():\n"
            "    assert True\n"
        )
        result = subprocess.run(
            [
                sys.executable, "-m", "pytest", str(bogus_marker_test),
                "--collect-only", "-q",
                # -c forces the repo's own pyproject.toml to load — --rootdir alone
                # doesn't, since config-file discovery walks up from the common
                # ancestor of cwd and the test path, which excludes REPO_ROOT here.
                "-c", str(REPO_ROOT / "pyproject.toml"),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode != 0, (
            f"expected --strict-markers to fail collection on an unregistered marker, "
            f"but exit was 0:\n{result.stdout}\n{result.stderr}"
        )
        # Matches just the marker name, not pytest's full diagnostic wording —
        # that wording is an internal message, not a documented API, and isn't
        # guaranteed stable across the pytest==8.* pin range.
        assert "definitely_not_a_real_marker" in result.stdout, (
            f"expected pytest's --strict-markers error message to name the bogus marker, "
            f"got:\n{result.stdout}\n{result.stderr}"
        )


class TestTimingMarkerCoverageParity:
    """Pins that `-m "not timing"` and `-m timing` are a complementary
    partition of the full collected suite: neither invocation errors out
    (returncode asserted below) and pytest's own mark-filtering isn't
    silently broken. This does NOT catch a test losing its `timing` marker
    — `not timing` and `timing` are complementary by construction, so a
    test moving between the two buckets leaves their sum unchanged
    regardless."""

    @staticmethod
    def _collected_node_ids(marker_expr: str | None) -> tuple[list[str], subprocess.CompletedProcess]:
        # -n0: unnecessary defensive redundancy, not a requirement — xdist's
        # own pytest_configure() unconditionally skips itself on
        # --collect-only regardless of -n, so this collect-only run was
        # never going to spawn workers even without it.
        args = [
            sys.executable, "-m", "pytest", "claude/.claude/",
            "--collect-only", "-q", "-n0", "--rootdir", str(REPO_ROOT),
        ]
        if marker_expr is not None:
            args += ["-m", marker_expr]
        result = subprocess.run(
            args,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        node_ids = [line for line in result.stdout.splitlines() if "::" in line]
        return node_ids, result

    def test_not_timing_plus_timing_selection_equals_unfiltered_total(self):
        not_timing_ids, not_timing_result = self._collected_node_ids("not timing")
        timing_ids, timing_result = self._collected_node_ids("timing")
        all_ids, all_result = self._collected_node_ids(None)

        for result in (not_timing_result, timing_result, all_result):
            assert result.returncode == 0, (
                f"collection failed (exit {result.returncode}):\n{result.stdout}\n{result.stderr}"
            )

        assert len(not_timing_ids) + len(timing_ids) == len(all_ids), (
            f'-m "not timing" selected {len(not_timing_ids)}, -m timing selected '
            f"{len(timing_ids)}, but the unfiltered run collected {len(all_ids)} — "
            "pytest's mark-filtering disagrees with itself across these three invocations"
        )
