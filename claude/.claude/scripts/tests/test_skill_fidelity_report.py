"""Tests for skill-fidelity-report.sh.

transcript-analysis.py is replaced in every test by a fake stand-in
co-located with a copy of the script under test, since the script always
resolves it via $(dirname "$0")/transcript-analysis.py -- a fixed relative
path next to the script itself, not something PATH or env can swap out.
"""
from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
from conftest import _make_repo_with_remote

# Path to the script under test (resolved relative to this file)
_SCRIPT = Path(__file__).parent.parent / "skill-fidelity-report.sh"


def _fake_transcript_analysis_source(
    *, skill_invocation_exit: int = 0, review_trace_exit: int = 0,
) -> str:
    """Source for a transcript-analysis.py stand-in that dispatches on its
    first positional argument, printing one fixed stdout line naming the
    resolved --branches value and exiting with the configured code."""
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys
        subcommand = sys.argv[1]
        branch = sys.argv[sys.argv.index("--branches") + 1]
        if subcommand == "skill-invocation":
            print("skill-invocation stdout for " + branch)
            sys.exit({skill_invocation_exit})
        elif subcommand == "review-trace":
            print("review-trace stdout for " + branch)
            sys.exit({review_trace_exit})
        else:
            sys.exit(2)
    """)


@pytest.fixture()
def make_script_fixture(tmp_path):
    """Factory: build a fixture directory holding a copy of the script
    under test plus a fake transcript-analysis.py, so $(dirname "$0")
    resolves to the fixture directory instead of the real scripts/ one.
    Returns the path to the copied script."""
    def _make(*, skill_invocation_exit: int = 0, review_trace_exit: int = 0) -> Path:
        fixture_dir = tmp_path / "fixture_scripts"
        fixture_dir.mkdir()
        script_copy = fixture_dir / "skill-fidelity-report.sh"
        shutil.copy(_SCRIPT, script_copy)
        script_copy.chmod(0o755)
        fake = fixture_dir / "transcript-analysis.py"
        fake.write_text(_fake_transcript_analysis_source(
            skill_invocation_exit=skill_invocation_exit,
            review_trace_exit=review_trace_exit,
        ))
        fake.chmod(0o755)
        return script_copy
    return _make


def _run_script(script_copy: Path, repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(script_copy)], cwd=str(repo), capture_output=True, text=True, check=False,
    )


class TestBothReportsSucceed:
    def test_both_reports_printed_in_order_and_exit_zero(self, tmp_path, make_script_fixture):
        repo, _bare = _make_repo_with_remote(tmp_path)
        script_copy = make_script_fixture()

        result = _run_script(script_copy, repo)

        assert result.returncode == 0
        skill_invocation_pos = result.stdout.index("=== skill-invocation ===")
        skill_invocation_body_pos = result.stdout.index("skill-invocation stdout for main")
        review_trace_pos = result.stdout.index("=== review-trace ===")
        review_trace_body_pos = result.stdout.index("review-trace stdout for main")
        assert skill_invocation_pos < skill_invocation_body_pos < review_trace_pos < review_trace_body_pos


class TestFirstCallFails:
    def test_review_trace_still_runs_and_exit_one(self, tmp_path, make_script_fixture):
        repo, _bare = _make_repo_with_remote(tmp_path)
        script_copy = make_script_fixture(skill_invocation_exit=1)

        result = _run_script(script_copy, repo)

        assert result.returncode == 1
        assert "review-trace stdout for main" in result.stdout
        assert "skill-fidelity-report.sh: skill-invocation report failed" in result.stderr
        assert "skill-fidelity-report.sh: review-trace report failed" not in result.stderr


class TestSecondCallFails:
    def test_skill_invocation_still_runs_and_exit_one(self, tmp_path, make_script_fixture):
        repo, _bare = _make_repo_with_remote(tmp_path)
        script_copy = make_script_fixture(review_trace_exit=1)

        result = _run_script(script_copy, repo)

        assert result.returncode == 1
        assert "skill-invocation stdout for main" in result.stdout
        assert "skill-fidelity-report.sh: review-trace report failed" in result.stderr
        assert "skill-fidelity-report.sh: skill-invocation report failed" not in result.stderr


class TestBothCallsFail:
    def test_exit_one_with_both_failure_notes(self, tmp_path, make_script_fixture):
        repo, _bare = _make_repo_with_remote(tmp_path)
        script_copy = make_script_fixture(skill_invocation_exit=1, review_trace_exit=1)

        result = _run_script(script_copy, repo)

        assert result.returncode == 1
        assert "skill-fidelity-report.sh: skill-invocation report failed" in result.stderr
        assert "skill-fidelity-report.sh: review-trace report failed" in result.stderr
