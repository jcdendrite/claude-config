"""Tests for session-marker-dashboard.sh.

The hook is a SessionStart hook whose stdout is injected by the harness
into the Claude session's conversation context. It surfaces existing
active-marker state so Claude can see which review-skill gates are
currently bypassed when resuming after compaction.

Output is emitted only when at least one active marker is present or
stale — an all-absent state (normal fresh session) produces no output.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from helpers import HOOKS_DIR

SESSION_MARKER_DASHBOARD_HOOK = HOOKS_DIR / "session-marker-dashboard.sh"


def _run_dashboard(payload: dict, isolated_home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(SESSION_MARKER_DASHBOARD_HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(isolated_home)},
        check=False,
    )


class TestSessionMarkerDashboard:
    def test_all_absent_produces_no_output(self, isolated_home):
        """When no active markers exist, the hook exits silently."""
        result = _run_dashboard({"session_id": "sess-absent"}, isolated_home)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_fresh_plan_review_marker_prints_dashboard(self, isolated_home):
        """A fresh plan-review-active marker triggers dashboard output."""
        sid = "sess-pr-active"
        marker_dir = isolated_home / ".claude" / ".plan-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).touch()
        result = _run_dashboard({"session_id": sid}, isolated_home)
        assert result.returncode == 0
        assert "plan-review-active" in result.stdout
        assert "present" in result.stdout

    def test_stale_plan_review_marker_shows_stale(self, isolated_home):
        """A >60-min-old active marker is labelled 'stale'."""
        sid = "sess-pr-stale"
        marker_dir = isolated_home / ".claude" / ".plan-review-active.d"
        marker_dir.mkdir(parents=True)
        marker = marker_dir / sid
        marker.touch()
        ninety_min_ago = time.time() - 90 * 60
        os.utime(marker, (ninety_min_ago, ninety_min_ago))
        result = _run_dashboard({"session_id": sid}, isolated_home)
        assert result.returncode == 0
        assert "stale" in result.stdout
        assert "plan-review-active" in result.stdout

    def test_respond_pr_marker_triggers_output(self, isolated_home):
        """A respond-pr-active marker also triggers the dashboard."""
        sid = "sess-rpr-active"
        marker_dir = isolated_home / ".claude" / ".respond-pr-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).touch()
        result = _run_dashboard({"session_id": sid}, isolated_home)
        assert result.returncode == 0
        assert "respond-pr-active" in result.stdout
        assert "present" in result.stdout

    def test_ready_for_review_marker_triggers_output(self, isolated_home):
        """A ready-for-review-active marker triggers the dashboard."""
        sid = "sess-rfr-active"
        marker_dir = isolated_home / ".claude" / ".ready-for-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).touch()
        result = _run_dashboard({"session_id": sid}, isolated_home)
        assert result.returncode == 0
        assert "ready-for-review-active" in result.stdout
        assert "present" in result.stdout

    def test_other_sessions_marker_does_not_trigger(self, isolated_home):
        """Session A's active marker must not appear in session B's dashboard."""
        marker_dir = isolated_home / ".claude" / ".plan-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / "session-A").touch()
        result = _run_dashboard({"session_id": "session-B"}, isolated_home)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_no_session_id_produces_no_output(self, isolated_home):
        """Missing session_id in the payload → hook exits silently."""
        result = _run_dashboard({}, isolated_home)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_all_three_markers_all_shown(self, isolated_home):
        """When all three skills have active markers, all three appear."""
        sid = "sess-all-active"
        for skill in ("plan-review", "ready-for-review", "respond-pr"):
            marker_dir = isolated_home / ".claude" / f".{skill}-active.d"
            marker_dir.mkdir(parents=True)
            (marker_dir / sid).touch()
        result = _run_dashboard({"session_id": sid}, isolated_home)
        assert result.returncode == 0
        assert "plan-review-active: present" in result.stdout
        assert "ready-for-review-active: present" in result.stdout
        assert "respond-pr-active: present" in result.stdout

    def test_exit_0_always(self, isolated_home):
        """Hook must always exit 0 to avoid blocking session startup."""
        result = _run_dashboard({"session_id": "sess-exit-check"}, isolated_home)
        assert result.returncode == 0
