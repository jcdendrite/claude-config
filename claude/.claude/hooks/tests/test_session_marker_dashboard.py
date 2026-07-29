"""Tests for session-marker-dashboard.sh.

The hook is a SessionStart hook (matcher: startup|clear|compact) that emits
hookSpecificOutput.additionalContext — the harness injects this JSON payload
into the agent's conversation context on session start, /clear, and /compact.
It surfaces existing active-marker state so Claude can see which review-skill
gates are currently bypassed when resuming after compaction.

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


def _additional_context(result: subprocess.CompletedProcess) -> str:
    """Parse the hookSpecificOutput.additionalContext from the hook's JSON output."""
    return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]


class TestSessionMarkerDashboard:
    def test_all_absent_produces_no_output(self, isolated_home):
        """When no active markers exist, the hook exits silently."""
        result = _run_dashboard({"session_id": "sess-absent"}, isolated_home)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_fresh_plan_review_marker_emits_json(self, isolated_home):
        """A fresh plan-review-active marker triggers JSON dashboard output."""
        sid = "sess-pr-active"
        marker_dir = isolated_home / ".claude" / ".plan-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).touch()
        result = _run_dashboard({"session_id": sid}, isolated_home)
        assert result.returncode == 0
        ctx = _additional_context(result)
        assert "plan-review-active" in ctx
        assert "present" in ctx

    def test_stale_plan_review_marker_shows_stale(self, isolated_home):
        """A >60-min-old active marker is labelled 'stale' in the context."""
        sid = "sess-pr-stale"
        marker_dir = isolated_home / ".claude" / ".plan-review-active.d"
        marker_dir.mkdir(parents=True)
        marker = marker_dir / sid
        marker.touch()
        ninety_min_ago = time.time() - 90 * 60
        os.utime(marker, (ninety_min_ago, ninety_min_ago))
        result = _run_dashboard({"session_id": sid}, isolated_home)
        assert result.returncode == 0
        ctx = _additional_context(result)
        assert "stale" in ctx
        assert "plan-review-active" in ctx

    def test_respond_pr_marker_triggers_output(self, isolated_home):
        """A respond-pr-active marker triggers dashboard output."""
        sid = "sess-rpr-active"
        marker_dir = isolated_home / ".claude" / ".respond-pr-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).touch()
        result = _run_dashboard({"session_id": sid}, isolated_home)
        assert result.returncode == 0
        ctx = _additional_context(result)
        assert "respond-pr-active" in ctx
        assert "present" in ctx

    def test_ready_for_review_marker_triggers_output(self, isolated_home):
        """A ready-for-review-active marker triggers dashboard output."""
        sid = "sess-rfr-active"
        marker_dir = isolated_home / ".claude" / ".ready-for-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).touch()
        result = _run_dashboard({"session_id": sid}, isolated_home)
        assert result.returncode == 0
        ctx = _additional_context(result)
        assert "ready-for-review-active" in ctx
        assert "present" in ctx

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
        """When all three skills have active markers, all three appear in context."""
        sid = "sess-all-active"
        for skill in ("plan-review", "ready-for-review", "respond-pr"):
            marker_dir = isolated_home / ".claude" / f".{skill}-active.d"
            marker_dir.mkdir(parents=True)
            (marker_dir / sid).touch()
        result = _run_dashboard({"session_id": sid}, isolated_home)
        assert result.returncode == 0
        ctx = _additional_context(result)
        assert "plan-review-active: present" in ctx
        assert "ready-for-review-active: present" in ctx
        assert "respond-pr-active: present" in ctx

    def test_output_is_valid_json_with_correct_shape(self, isolated_home):
        """When markers are active, output must be parseable JSON with hookEventName + additionalContext."""
        sid = "sess-json-shape"
        marker_dir = isolated_home / ".claude" / ".plan-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).touch()
        result = _run_dashboard({"session_id": sid}, isolated_home)
        assert result.returncode == 0
        parsed = json.loads(result.stdout)
        hook_output = parsed["hookSpecificOutput"]
        assert hook_output["hookEventName"] == "SessionStart"
        assert isinstance(hook_output["additionalContext"], str)

    def test_exit_0_always(self, isolated_home):
        """Hook must always exit 0 to avoid blocking session startup."""
        result = _run_dashboard({"session_id": "sess-exit-check"}, isolated_home)
        assert result.returncode == 0

    def test_traversal_session_id_produces_no_output(self, isolated_home):
        """A traversal session_id must not make this read-only hook report
        marker status for a file outside the three *-active.d/ directories.

        All three directories (.plan-review-active.d, .ready-for-review-active.d,
        .respond-pr-active.d) sit one level under ~/.claude, so a session_id of
        '../canary' resolves every marker_status() call to the same file —
        planting a canary there would make all three read as "present",
        which is the discriminating signal: with the guard, the hook exits
        before any marker_status() call and produces the documented
        all-absent disposition (no output); without it, the canary would be
        misreported as three present markers and the hook would emit a
        dashboard payload.

        At least one *-active.d directory must already exist for this to be
        meaningful: `[ -f ]` on a path that walks '..' through a nonexistent
        directory component fails closed (ENOENT) regardless of the guard,
        which would make the traversal inert by accident."""
        marker_dir = isolated_home / ".claude" / ".plan-review-active.d"
        marker_dir.mkdir(parents=True)
        canary = isolated_home / ".claude" / "canary"
        canary.write_text("untouched\n")

        result = _run_dashboard({"session_id": "../canary"}, isolated_home)

        assert result.returncode == 0
        assert result.stdout == ""
        assert canary.read_text() == "untouched\n"
