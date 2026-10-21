"""Tests for review-pr-reviewer-findings-dir.sh -- creates the per-session
directory /review-pr's reviewers write their findings files into, and prints
its path.

Every test that succeeds in creating the directory needs a writable,
unsandboxed literal /tmp. See the comment on _lib_review_pr_reviewer_findings_dir
in _lib.sh for why the root is never $TMPDIR.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from helpers import SCRIPTS_DIR

from .conftest import _seed_session

SCRIPT = SCRIPTS_DIR / "review-pr-reviewer-findings-dir.sh"

# A harness bound so a hung bash fails one test instead of the suite.
_SUBPROCESS_TIMEOUT_SECONDS = 60


@pytest.fixture
def isolated_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    return home


def _run(
    home: Path, args: list[str] | None = None, extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    env = {"HOME": str(home), "PATH": os.environ["PATH"], **(extra_env or {})}
    return subprocess.run(
        ["bash", str(SCRIPT), *(args or [])], env=env, capture_output=True, text=True,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    )


class TestUsageErrors:
    def test_any_argument_exits_two_with_usage(self, isolated_home):
        result = _run(isolated_home, ["unexpected"])
        assert result.returncode == 2
        assert "Usage" in result.stderr
        assert result.stdout == ""


class TestCreatesTheDirectory:
    def test_prints_the_exact_path_and_the_directory_exists(self, isolated_home, reviewer_findings_session):
        session_id, findings_dir = reviewer_findings_session
        _seed_session(isolated_home, session_id)

        result = _run(isolated_home)

        assert result.returncode == 0, result.stderr
        assert result.stdout == f"{findings_dir}\n"
        assert findings_dir.is_dir()

    def test_a_second_run_prints_the_same_path(self, isolated_home, reviewer_findings_session):
        session_id, findings_dir = reviewer_findings_session
        _seed_session(isolated_home, session_id)
        first = _run(isolated_home)
        (findings_dir / "staff-sdet-1700000000-branch.md").write_text("findings\n")

        second = _run(isolated_home)

        assert first.returncode == 0, first.stderr
        assert second.returncode == 0, second.stderr
        assert second.stdout == first.stdout
        assert (findings_dir / "staff-sdet-1700000000-branch.md").exists(), "a re-run must not clear the directory"

    def test_tmpdir_does_not_move_the_directory(self, isolated_home, reviewer_findings_session, tmp_path):
        """The printed directory stays under the literal /tmp when TMPDIR points
        elsewhere."""
        session_id, findings_dir = reviewer_findings_session
        _seed_session(isolated_home, session_id)

        result = _run(isolated_home, extra_env={"TMPDIR": str(tmp_path)})

        assert result.returncode == 0, result.stderr
        assert result.stdout == f"{findings_dir}\n"


class TestFailuresExitOneWithNothingOnStdout:
    def test_a_regular_file_at_the_path_makes_mkdir_fail(self, isolated_home, reviewer_findings_session):
        session_id, findings_dir = reviewer_findings_session
        _seed_session(isolated_home, session_id)
        findings_dir.write_text("not a directory\n")

        result = _run(isolated_home)

        assert result.returncode == 1
        assert result.stdout == ""
        assert str(findings_dir) in result.stderr
        create_failure_lines = [line for line in result.stderr.splitlines() if "could not create" in line]
        assert len(create_failure_lines) == 1, result.stderr
        assert "File exists" in create_failure_lines[0], "mkdir ran in the C locale, so its error text is English"

    def test_no_live_session_exits_one(self, isolated_home):
        """No capture-session-id.sh SessionStart hook ever ran for this
        process, so there is no session id to build a path from."""
        result = _run(isolated_home)

        assert result.returncode == 1
        assert result.stdout == ""
        assert "could not resolve this session's id" in result.stderr

    def test_marker_sh_rejects_a_session_id_that_is_not_a_path_component(self, isolated_home):
        _seed_session(isolated_home, "a.b")

        result = _run(isolated_home)

        assert result.returncode == 1
        assert result.stdout == ""
        assert "could not resolve this session's id" in result.stderr
