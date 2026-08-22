"""Tests for pr-cost-section.sh.

transcript-analysis.py is replaced in every test by a fake stand-in
co-located with a copy of the script under test, since the script always
resolves it via $(dirname "$0")/transcript-analysis.py -- a fixed relative
path next to the script itself, not something PATH or env can swap out.
The script also sources ../hooks/_lib.sh relative to its own location, so
the fixture directory mirrors that layout with a copy of the real _lib.sh.

CLAUDE_CONFIG_DIR is pinned per test via subprocess env, isolated from this
machine's real ~/.claude, to control the pr-cost-disclosure sentinel file's
presence and content.
"""
from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
from conftest import _base_test_env, _make_repo_with_remote

# Path to the script under test (resolved relative to this file)
_SCRIPT = Path(__file__).parent.parent / "pr-cost-section.sh"
_LIB_SH = Path(__file__).parent.parent.parent / "hooks" / "_lib.sh"


def _fake_transcript_analysis_source() -> str:
    """Source for a transcript-analysis.py stand-in: echoes its own argv (so
    a test can assert the exact invocation shape) plus a fixed cost-report
    body the exit-0 case asserts verbatim."""
    return textwrap.dedent("""\
        #!/usr/bin/env python3
        import sys
        print("ARGS: " + " ".join(sys.argv[1:]))
        print("total: $12.34")
    """)


@pytest.fixture()
def script_fixture(tmp_path) -> Path:
    """Build a fixture directory holding a copy of the script under test, a
    copy of _lib.sh at the relative path it sources, and a fake
    transcript-analysis.py. Returns the path to the copied script."""
    fixture_root = tmp_path / "fixture_root"
    scripts_dir = fixture_root / "scripts"
    hooks_dir = fixture_root / "hooks"
    scripts_dir.mkdir(parents=True)
    hooks_dir.mkdir(parents=True)

    script_copy = scripts_dir / "pr-cost-section.sh"
    shutil.copy(_SCRIPT, script_copy)
    script_copy.chmod(0o755)

    shutil.copy(_LIB_SH, hooks_dir / "_lib.sh")

    fake = scripts_dir / "transcript-analysis.py"
    fake.write_text(_fake_transcript_analysis_source())
    fake.chmod(0o755)

    return script_copy


def _run_script(script_copy: Path, cwd: Path, config_dir: Path) -> subprocess.CompletedProcess:
    env = {**_base_test_env(), "CLAUDE_CONFIG_DIR": str(config_dir)}
    return subprocess.run(
        [str(script_copy)], cwd=str(cwd), capture_output=True, text=True, check=False, env=env,
    )


def _write_sentinel(config_dir: Path, content: str) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "pr-cost-disclosure").write_text(content)


class TestSentinelEnabledBranchResolves:
    def test_prints_cost_report_verbatim_and_exit_zero(self, tmp_path, script_fixture):
        repo, _bare = _make_repo_with_remote(tmp_path)
        config_dir = tmp_path / "claude_config"
        _write_sentinel(config_dir, "dollars\n")

        result = _run_script(script_fixture, repo, config_dir)

        assert result.returncode == 0
        assert result.stdout == "ARGS: cost --this-repo --branches main --summary\ntotal: $12.34\n"


class TestSentinelMixedCase:
    """The script explicitly lowercases before comparing -- a regression to
    that step would silently flip real users' DOLLARS/Dollars sentinels from
    enabled to disabled with no other test catching it."""

    def test_uppercase_sentinel_still_enables(self, tmp_path, script_fixture):
        repo, _bare = _make_repo_with_remote(tmp_path)
        config_dir = tmp_path / "claude_config"
        _write_sentinel(config_dir, "DOLLARS\n")

        result = _run_script(script_fixture, repo, config_dir)

        assert result.returncode == 0
        assert result.stdout == "ARGS: cost --this-repo --branches main --summary\ntotal: $12.34\n"


class TestSentinelAbsent:
    def test_no_stdout_and_exit_one(self, tmp_path, script_fixture):
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        config_dir = tmp_path / "claude_config"
        config_dir.mkdir()

        result = _run_script(script_fixture, cwd, config_dir)

        assert result.returncode == 1
        assert result.stdout == ""


class TestSentinelWrongValue:
    def test_no_stdout_and_exit_one(self, tmp_path, script_fixture):
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        config_dir = tmp_path / "claude_config"
        _write_sentinel(config_dir, "usd\n")

        result = _run_script(script_fixture, cwd, config_dir)

        assert result.returncode == 1
        assert result.stdout == ""


class TestSentinelEnabledDetachedHead:
    def test_no_stdout_and_exit_two(self, tmp_path, script_fixture):
        repo, _bare = _make_repo_with_remote(tmp_path)
        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True,
        ).stdout.strip()
        subprocess.run(["git", "checkout", "-q", head_sha], cwd=repo, check=True)
        config_dir = tmp_path / "claude_config"
        _write_sentinel(config_dir, "dollars\n")

        result = _run_script(script_fixture, repo, config_dir)

        assert result.returncode == 2
        assert result.stdout == ""


class TestSentinelBlankLineThenDollars:
    """Guards the deliberate narrowing: a leading blank line makes the
    sentinel read as two lines, judged disabled -- a future edit that
    widens the trim to collapse interior/leading newlines must fail this."""

    def test_judged_disabled_not_enabled(self, tmp_path, script_fixture):
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        config_dir = tmp_path / "claude_config"
        _write_sentinel(config_dir, "\ndollars\n")

        result = _run_script(script_fixture, cwd, config_dir)

        assert result.returncode == 1
        assert result.stdout == ""
