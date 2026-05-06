"""Tests for claude/.claude/scripts/marker.sh."""
from __future__ import annotations

import os
import subprocess

import pytest
from helpers import SCRIPTS_DIR

MARKER_SCRIPT = SCRIPTS_DIR / "marker.sh"


def _run(args: list[str], cwd, home) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(MARKER_SCRIPT)] + args,
        cwd=cwd,
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True,
    )


class TestMarkerScriptSessionMissing:
    """When the session file ($HOME/.claude/sessions/$PPID) does not exist,
    every write/activate/deactivate subcommand must exit 2 and write nothing.
    This guards against the silent-empty-suffix regression: without explicit
    || exit 2 on the command-substitution call sites, exit 2 inside
    _resolve_session_id() only exits the subshell — the parent continues and
    writes a malformed marker with an empty session-id suffix."""

    @pytest.mark.parametrize(
        "args",
        [
            ["write", "code-review"],
            ["write", "skill-review"],
            ["write", "plan-review"],
            ["write", "ready-for-review"],
            ["activate", "plan-review"],
            ["activate", "ready-for-review"],
            ["activate", "respond-pr"],
            ["deactivate", "plan-review"],
            ["deactivate", "ready-for-review"],
            ["deactivate", "respond-pr"],
        ],
    )
    def test_exits_2_when_session_file_missing(self, isolated_home, git_repo, args):
        result = _run(args, cwd=git_repo, home=isolated_home)
        assert result.returncode == 2, (
            f"marker.sh {' '.join(args)} should exit 2 when session file is absent, "
            f"got {result.returncode}. stderr: {result.stderr!r}"
        )

    def test_no_marker_written_when_session_file_missing(self, isolated_home, git_repo):
        _run(["write", "code-review"], cwd=git_repo, home=isolated_home)
        marker_dir = isolated_home / ".claude" / "review-markers"
        stray = list(marker_dir.iterdir()) if marker_dir.exists() else []
        assert stray == [], (
            f"marker.sh wrote a stray marker when session file was absent: {stray}"
        )


class TestMarkerScriptHappyPath:
    """Smoke-test that each subcommand writes/removes the expected file when
    the session file is present."""

    def _seed_session(self, home):
        sessions_dir = home / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        sid = "test-session-abc"
        (sessions_dir / str(os.getpid())).write_text(sid)
        return sid

    def test_write_code_review_creates_marker(self, isolated_home, git_repo):
        sid = self._seed_session(isolated_home)
        result = _run(["write", "code-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        marker_dir = isolated_home / ".claude" / "review-markers"
        files = list(marker_dir.iterdir())
        assert len(files) == 1
        assert files[0].name.endswith(f".{sid}")

    def test_activate_creates_active_marker(self, isolated_home, git_repo):
        sid = self._seed_session(isolated_home)
        result = _run(["activate", "plan-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        active_file = isolated_home / ".claude" / ".plan-review-active.d" / sid
        assert active_file.exists()

    def test_deactivate_removes_active_marker(self, isolated_home, git_repo):
        sid = self._seed_session(isolated_home)
        active_dir = isolated_home / ".claude" / ".plan-review-active.d"
        active_dir.mkdir(parents=True)
        (active_dir / sid).touch()
        result = _run(["deactivate", "plan-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        assert not (active_dir / sid).exists()

    def test_help_exits_0(self, isolated_home, git_repo):
        result = _run(["--help"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0
