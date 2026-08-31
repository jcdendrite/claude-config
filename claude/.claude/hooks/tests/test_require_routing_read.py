"""Tests for require-routing-read.sh."""
from __future__ import annotations

import os
import time

from helpers import (
    HOOKS_DIR,
    SKILLS_DIR,
    agent_input,
    assert_gate_handles_traversal_session_id,
    bash_input,
    extract_skill_command,
    run_hook,
    run_hook_reason,
    run_skill_command,
    write_plan_review_active_marker,
    write_plan_review_routing_read_marker,
)

from .conftest import _seed_session

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

    def test_deny_message_names_read_tool(self, isolated_home):
        """Deny message names the Read tool, so a Bash-based read isn't mistaken for satisfying the gate."""
        sid = "session-deny-msg-read-tool"
        write_plan_review_active_marker(isolated_home, sid)
        reason = run_hook_reason(REQUIRE_ROUTING_READ_HOOK, agent_input(session_id=sid))
        assert reason is not None
        assert "Read tool" in reason
        assert "does not satisfy this gate" in reason

    def test_deactivate_gate_fixture_removes_routing_read_marker(
        self, isolated_home, tmp_path
    ):
        """SKILL.md deactivate-gate recipe removes both markers (aligned with hook)."""
        sid = "session-deactivate-both"
        repo = tmp_path / "repo"
        repo.mkdir()

        _seed_session(isolated_home, sid)

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

    # -- CLAUDE_CONFIG_DIR ------------------------------------------------

    def test_active_marker_under_config_dir_without_routing_read_denies(
        self, isolated_home, tmp_path
    ):
        """CLAUDE_CONFIG_DIR set: the active marker is read from the
        resolved config dir, not $HOME/.claude."""
        sid = "session-config-dir-no-read"
        config_dir = tmp_path / "profile"
        active_dir = config_dir / ".plan-review-active.d"
        active_dir.mkdir(parents=True)
        (active_dir / sid).touch()
        assert run_hook(
            REQUIRE_ROUTING_READ_HOOK,
            agent_input(session_id=sid),
            home=isolated_home,
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        ) == "deny"

    def test_active_marker_under_config_dir_with_routing_read_allows(
        self, isolated_home, tmp_path
    ):
        """CLAUDE_CONFIG_DIR set: a fresh routing-read marker under the
        resolved config dir authorizes the Agent spawn."""
        sid = "session-config-dir-with-read"
        config_dir = tmp_path / "profile"
        (config_dir / ".plan-review-active.d").mkdir(parents=True)
        (config_dir / ".plan-review-active.d" / sid).touch()
        (config_dir / ".plan-review-routing-read.d").mkdir(parents=True)
        (config_dir / ".plan-review-routing-read.d" / sid).touch()
        assert run_hook(
            REQUIRE_ROUTING_READ_HOOK,
            agent_input(session_id=sid),
            home=isolated_home,
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        ) == "allow"

    def test_legacy_home_marker_ignored_when_config_dir_set(self, isolated_home, tmp_path):
        """Config-dir resolution is a swap, not a union: an active marker at
        the legacy $HOME/.claude location must not satisfy the gate once
        CLAUDE_CONFIG_DIR points elsewhere."""
        sid = "session-legacy-ignored"
        write_plan_review_active_marker(isolated_home, sid)
        config_dir = tmp_path / "profile"
        config_dir.mkdir(parents=True)
        assert run_hook(
            REQUIRE_ROUTING_READ_HOOK,
            agent_input(session_id=sid),
            home=isolated_home,
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        ) == "allow"

    def test_relative_config_dir_fails_open(self, isolated_home):
        """CLAUDE_CONFIG_DIR set to a relative value cannot be resolved, so
        the gate allows rather than falling back to the legacy
        $HOME/.claude marker (which, if read, would deny here)."""
        sid = "session-relative-config-dir"
        write_plan_review_active_marker(isolated_home, sid)
        assert run_hook(
            REQUIRE_ROUTING_READ_HOOK,
            agent_input(session_id=sid),
            home=isolated_home,
            extra_env={"CLAUDE_CONFIG_DIR": "relative/path"},
        ) == "allow"

    # -- Hostile session_id ---------------------------------------------------

    def test_traversal_session_id_allows_and_does_not_touch_marker_dir(
        self, isolated_home
    ):
        """Pins the disposition an unusable session_id gets from this gate,
        and that nothing outside the marker directory is written.

        This gate is activation-shaped: ACTIVE_MARKER's presence turns
        enforcement on, and its absence means plan-review is not running, so
        the standing default is allow. An unusable session_id leaves that
        question unanswerable — the same position as an absent id — so it
        allows too.

        The bypass-shaped gates (require-memory-skill.sh,
        require-respond-pr.sh) invert this: there the marker grants an
        exception to a standing deny, so an unusable id withholds the
        exception and the gate denies. Their sibling tests assert 'deny' for
        the identical input, and that difference is intentional.

        What this test does NOT pin: that the guard prevented the traversed
        path from being built. It cannot. ACTIVE_MARKER and ROUTING_MARKER
        embed the same session_id at the same depth under sibling
        directories, so a traversing id resolves both to one file — which
        then reads as an active session whose routing marker is fresh, i.e.
        allow. Guard present and guard absent produce the same verdict here,
        so no assertion can separate them. The guard still belongs (this hook
        must not build attacker-shaped paths, and its absence is a real
        defect in the write-sink hooks), but the property is pinned by
        test_lib.py's direct unit tests of _lib_valid_session_id_component
        and by the write-sink hooks' own traversal tests, not by this one."""
        assert_gate_handles_traversal_session_id(
            REQUIRE_ROUTING_READ_HOOK,
            lambda sid: agent_input(session_id=sid),
            isolated_home,
            expected_decision="allow",
        )
