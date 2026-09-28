"""Tests for review-pr-findings-path.sh -- prints the fixed findings-body
path for /review-pr's synthesize-and-record step, so SKILL.md's Write-tool
call gets the path from a script rather than transcribing
$CONFIG_DIR/$SESSION_ID by hand.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from helpers import SCRIPTS_DIR

from .conftest import _seed_session

SCRIPT = SCRIPTS_DIR / "review-pr-findings-path.sh"
SID = "test-session-review-pr-findings-path"


@pytest.fixture
def isolated_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    return home


def _write_provenance(
    home: Path, pr_identity: str = "foo/bar#42", head_ref_oid: str = "a" * 40,
    mode: str = "acquired", pid: int = 999, session_id: str = SID,
) -> Path:
    active_dir = home / ".claude" / ".review-pr-active.d"
    active_dir.mkdir(parents=True, exist_ok=True)
    provenance = active_dir / f"{session_id}.provenance"
    # Field order matches the completion marker's own (PR identity,
    # headRefOid, PID, mode): PID is the third field, not mode.
    provenance.write_text(f"{pr_identity}\n{head_ref_oid}\n{pid}\n{mode}\n")
    return provenance


def _run(home: Path, args: list[str] | None = None) -> subprocess.CompletedProcess:
    env = {"HOME": str(home)}
    env.pop("CLAUDE_CONFIG_DIR", None)
    return subprocess.run(
        ["bash", str(SCRIPT), *(args or [])], env=env, capture_output=True, text=True,
    )


class TestUsageErrors:
    def test_any_argument_exits_two_with_usage(self, isolated_home):
        result = _run(isolated_home, ["unexpected"])
        assert result.returncode == 2
        assert "Usage" in result.stderr


class TestNoProvenance:
    def test_missing_provenance_file_exits_two(self, isolated_home):
        _seed_session(isolated_home, SID)
        result = _run(isolated_home)
        assert result.returncode == 2
        assert "provenance" in result.stderr


class TestProvenancePresent:
    def test_prints_the_fixed_body_path(self, isolated_home):
        _seed_session(isolated_home, SID)
        _write_provenance(isolated_home)
        result = _run(isolated_home)
        assert result.returncode == 0, result.stderr
        expected = isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.body"
        assert result.stdout.strip() == str(expected)

    def test_path_is_derived_the_same_way_regardless_of_provenance_mode(self, isolated_home):
        """The printed path only depends on the session id -- mode
        acquired/checkout/diff-only must all resolve identically, since the
        path is a fixed derivation, not something provenance's own fields
        vary."""
        _seed_session(isolated_home, SID)
        _write_provenance(isolated_home, mode="diff-only")
        result = _run(isolated_home)
        assert result.returncode == 0, result.stderr
        expected = isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.body"
        assert result.stdout.strip() == str(expected)


class TestNoLiveSession:
    def test_no_session_file_exits_two(self, isolated_home):
        """No capture-session-id.sh SessionStart hook ever ran for this
        process -- marker.sh resolve-session-id must fail closed, never
        printing a path built from an unresolvable session id."""
        result = _run(isolated_home)
        assert result.returncode == 2
