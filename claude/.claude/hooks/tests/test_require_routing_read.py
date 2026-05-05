"""Tests for require-routing-read.sh."""
from __future__ import annotations

import os
import time

import pytest
from helpers import (
    HOOKS_DIR,
    SKILLS_DIR,
    agent_input,
    bash_input,
    extract_skill_command,
    plan_review_active_marker_path,
    plan_review_routing_read_marker_path,
    run_hook,
    run_hook_reason,
    run_skill_command,
    write_plan_review_active_marker,
    write_plan_review_routing_read_marker,
)

REQUIRE_ROUTING_READ_HOOK = HOOKS_DIR / "require-routing-read.sh"
PLAN_REVIEW_SKILL = SKILLS_DIR / "plan-review" / "SKILL.md"


class TestRequireRoutingRead:
    def test_no_active_marker_allows_agent(self, isolated_home):
        """When plan-review is not active, Agent spawns pass through."""
        sid = "session-no-active"
        assert run_hook(REQUIRE_ROUTING_READ_HOOK, agent_input(session_id=sid)) == "allow"

    def test_active_marker_without_routing_read_denies(self, isolated_home):
        """Active plan-review session without ROUTING.md Read → deny."""
        sid = "session-active-no-read"
        write_plan_review_active_marker(isolated_home, sid)
        assert run_hook(REQUIRE_ROUTING_READ_HOOK, agent_input(session_id=sid)) == "deny"

    def test_active_marker_with_routing_read_allows(self, isolated_home):
        """Active plan-review session + fresh routing-read marker → allow."""
        sid = "session-active-with-read"
        write_plan_review_active_marker(isolated_home, sid)
        write_plan_review_routing_read_marker(isolated_home, sid)
        assert run_hook(REQUIRE_ROUTING_READ_HOOK, agent_input(session_id=sid)) == "allow"

    def test_stale_routing_read_marker_denies(self, isolated_home):
        """Routing-read marker older than 60 min is treated as absent → deny."""
        sid = "session-stale-read"
        write_plan_review_active_marker(isolated_home, sid)
        marker = write_plan_review_routing_read_marker(isolated_home, sid)
        # Age the marker past 60 minutes.
        stale_time = time.time() - 3700
        os.utime(marker, (stale_time, stale_time))
        assert run_hook(REQUIRE_ROUTING_READ_HOOK, agent_input(session_id=sid)) == "deny"

    def test_wrong_tool_name_allows(self, isolated_home):
        """Defense-in-depth: non-Agent tool is never denied."""
        sid = "session-wrong-tool"
        write_plan_review_active_marker(isolated_home, sid)
        assert run_hook(REQUIRE_ROUTING_READ_HOOK, bash_input("ls", session_id=sid)) == "allow"

    def test_missing_session_id_allows(self, isolated_home):
        """No session_id → allow (can't check markers)."""
        assert run_hook(REQUIRE_ROUTING_READ_HOOK, {"tool_name": "Agent", "tool_input": {}}) == "allow"

    def test_cross_session_routing_read_does_not_authorize(self, isolated_home):
        """Marker from session A does not authorize session B."""
        sid_a = "session-a-with-read"
        sid_b = "session-b-no-read"
        write_plan_review_active_marker(isolated_home, sid_b)
        write_plan_review_routing_read_marker(isolated_home, sid_a)
        assert run_hook(REQUIRE_ROUTING_READ_HOOK, agent_input(session_id=sid_b)) == "deny"

    def test_deny_message_names_routing_md(self, isolated_home):
        """Deny message instructs model to read ROUTING.md."""
        sid = "session-deny-msg"
        write_plan_review_active_marker(isolated_home, sid)
        reason = run_hook_reason(REQUIRE_ROUTING_READ_HOOK, agent_input(session_id=sid))
        assert reason is not None
        assert "ROUTING.md" in reason

    def test_deactivate_gate_fixture_removes_routing_read_marker(
        self, isolated_home, tmp_path
    ):
        """SKILL.md deactivate-gate recipe removes both markers (aligned with hook)."""
        sid = "session-deactivate-both"
        repo = tmp_path / "repo"
        repo.mkdir()

        sessions_dir = isolated_home / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / str(os.getpid())).write_text(sid)

        active_marker = write_plan_review_active_marker(isolated_home, sid)
        routing_marker = write_plan_review_routing_read_marker(isolated_home, sid)

        run_skill_command(
            extract_skill_command(PLAN_REVIEW_SKILL, "deactivate-gate"),
            cwd=repo,
            isolated_home=isolated_home,
        )

        assert not active_marker.exists(), (
            "deactivate-gate did not remove the active-session marker"
        )
        assert not routing_marker.exists(), (
            "deactivate-gate did not remove the routing-read marker"
        )
