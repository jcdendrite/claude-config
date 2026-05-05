"""Tests for log-routing-read.sh."""
from __future__ import annotations

from helpers import (
    HOOKS_DIR,
    agent_input,
    plan_review_routing_read_marker_path,
    read_input,
    run_hook,
    write_plan_review_active_marker,
)

LOG_ROUTING_READ_HOOK = HOOKS_DIR / "log-routing-read.sh"

ROUTING_MD_PATH = "/home/user/.claude/skills/plan-review/ROUTING.md"
OTHER_FILE_PATH = "/home/user/.claude/skills/plan-review/SKILL.md"


class TestLogRoutingRead:
    def test_read_routing_md_with_active_marker_writes_routing_read_marker(
        self, isolated_home
    ):
        sid = "session-log-routing"
        write_plan_review_active_marker(isolated_home, sid)
        run_hook(LOG_ROUTING_READ_HOOK, read_input(ROUTING_MD_PATH, session_id=sid))
        assert plan_review_routing_read_marker_path(isolated_home, sid).exists()

    def test_read_routing_md_without_active_marker_is_noop(self, isolated_home):
        sid = "session-no-active"
        run_hook(LOG_ROUTING_READ_HOOK, read_input(ROUTING_MD_PATH, session_id=sid))
        assert not plan_review_routing_read_marker_path(isolated_home, sid).exists()

    def test_read_unrelated_file_is_noop(self, isolated_home):
        sid = "session-other-file"
        write_plan_review_active_marker(isolated_home, sid)
        run_hook(LOG_ROUTING_READ_HOOK, read_input(OTHER_FILE_PATH, session_id=sid))
        assert not plan_review_routing_read_marker_path(isolated_home, sid).exists()

    def test_wrong_tool_name_is_noop(self, isolated_home):
        sid = "session-wrong-tool"
        write_plan_review_active_marker(isolated_home, sid)
        run_hook(LOG_ROUTING_READ_HOOK, agent_input(session_id=sid))
        assert not plan_review_routing_read_marker_path(isolated_home, sid).exists()

    def test_worktree_path_variant_writes_marker(self, isolated_home):
        """Verify the glob matches both stowed and worktree-relative ROUTING.md paths."""
        sid = "session-worktree-path"
        write_plan_review_active_marker(isolated_home, sid)
        worktree_path = "/home/user/MyCode/repo/.claude/worktrees/branch/claude/.claude/skills/plan-review/ROUTING.md"
        run_hook(LOG_ROUTING_READ_HOOK, read_input(worktree_path, session_id=sid))
        assert plan_review_routing_read_marker_path(isolated_home, sid).exists()

    def test_idempotent_second_read_leaves_marker(self, isolated_home):
        sid = "session-idempotent"
        write_plan_review_active_marker(isolated_home, sid)
        run_hook(LOG_ROUTING_READ_HOOK, read_input(ROUTING_MD_PATH, session_id=sid))
        run_hook(LOG_ROUTING_READ_HOOK, read_input(ROUTING_MD_PATH, session_id=sid))
        assert plan_review_routing_read_marker_path(isolated_home, sid).exists()

    def test_missing_session_id_is_noop(self, isolated_home):
        run_hook(LOG_ROUTING_READ_HOOK, {"tool_name": "Read", "tool_input": {"file_path": ROUTING_MD_PATH}})
        routing_read_dir = isolated_home / ".claude" / ".plan-review-routing-read.d"
        assert not routing_read_dir.exists() or not any(routing_read_dir.iterdir())

    def test_hook_always_exits_allow(self, isolated_home):
        sid = "session-always-allow"
        write_plan_review_active_marker(isolated_home, sid)
        assert run_hook(LOG_ROUTING_READ_HOOK, read_input(ROUTING_MD_PATH, session_id=sid)) == "allow"
