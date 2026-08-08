"""Tests for log-routing-read.sh."""
from __future__ import annotations

from helpers import (
    CANARY_CONTENT,
    HOOKS_DIR,
    TRAVERSAL_SESSION_ID,
    agent_input,
    plan_review_pending_read_marker_path,
    plan_review_routing_read_marker_path,
    plant_traversal_canary,
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

    def test_read_routing_md_without_active_marker_writes_pending_read_only(
        self, isolated_home
    ):
        """No active marker yet: the routing-read marker itself is still not
        written directly, but the pending-read record now is (unconditional
        write) -- this is the raw material marker.sh activate's bounded-
        window backfill later reads to credit a Read that happened first."""
        sid = "session-no-active"
        run_hook(LOG_ROUTING_READ_HOOK, read_input(ROUTING_MD_PATH, session_id=sid))
        assert not plan_review_routing_read_marker_path(isolated_home, sid).exists()
        assert plan_review_pending_read_marker_path(isolated_home, sid).exists()

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
        pending_read_dir = isolated_home / ".claude" / ".plan-review-pending-read.d"
        assert not pending_read_dir.exists() or not any(pending_read_dir.iterdir())

    def test_hook_always_exits_allow(self, isolated_home):
        sid = "session-always-allow"
        write_plan_review_active_marker(isolated_home, sid)
        assert run_hook(LOG_ROUTING_READ_HOOK, read_input(ROUTING_MD_PATH, session_id=sid)) == "allow"

    # -- CLAUDE_CONFIG_DIR ------------------------------------------------

    def test_read_routing_md_with_active_marker_under_config_dir_writes_marker_there(
        self, isolated_home, tmp_path
    ):
        """CLAUDE_CONFIG_DIR set: the routing-read marker is written under
        the resolved config dir, not $HOME/.claude."""
        sid = "session-config-dir-write"
        config_dir = tmp_path / "profile"
        (config_dir / ".plan-review-active.d").mkdir(parents=True)
        (config_dir / ".plan-review-active.d" / sid).touch()
        run_hook(
            LOG_ROUTING_READ_HOOK,
            read_input(ROUTING_MD_PATH, session_id=sid),
            home=isolated_home,
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        )
        assert (config_dir / ".plan-review-routing-read.d" / sid).exists()
        assert not plan_review_routing_read_marker_path(isolated_home, sid).exists()

    def test_legacy_home_active_marker_ignored_when_config_dir_set(self, isolated_home, tmp_path):
        """Config-dir resolution is a swap, not a union: an active marker at
        the legacy $HOME/.claude location must not authorize a write once
        CLAUDE_CONFIG_DIR points elsewhere."""
        sid = "session-legacy-write-ignored"
        write_plan_review_active_marker(isolated_home, sid)
        config_dir = tmp_path / "profile"
        config_dir.mkdir(parents=True)
        run_hook(
            LOG_ROUTING_READ_HOOK,
            read_input(ROUTING_MD_PATH, session_id=sid),
            home=isolated_home,
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        )
        assert not (config_dir / ".plan-review-routing-read.d" / sid).exists()

    def test_relative_config_dir_fails_open_without_writing(self, isolated_home):
        """CLAUDE_CONFIG_DIR set to a relative value cannot be resolved, so
        the write is skipped rather than falling back to the legacy
        $HOME/.claude marker directory."""
        sid = "session-relative-config-dir"
        write_plan_review_active_marker(isolated_home, sid)
        run_hook(
            LOG_ROUTING_READ_HOOK,
            read_input(ROUTING_MD_PATH, session_id=sid),
            home=isolated_home,
            extra_env={"CLAUDE_CONFIG_DIR": "relative/path"},
        )
        assert not plan_review_routing_read_marker_path(isolated_home, sid).exists()

    def test_traversal_session_id_allows_and_does_not_touch_marker_dir(self, isolated_home):
        """A session_id of '../canary' must not reach this hook's write.

        ACTIVE_MARKER (.plan-review-active.d/$SESSION_ID) and the
        routing-read write target (.plan-review-routing-read.d/$SESSION_ID)
        are sibling directories at the same depth under ~/.claude, so a
        traversing id resolves both to one file. The canary planted there is
        therefore both what makes ACTIVE_MARKER's existence check pass — a
        precondition for reaching the touch at all — and the file the touch
        would act on.

        Assert on mtime, not content: the write is a `touch`, which never
        changes content, so a content-equality assertion passes whether or
        not the guard ran and pins nothing. mtime is what separates the two
        states — guard present, the hook exits before the touch and mtime
        holds; guard absent, the touch executes and mtime advances.

        .plan-review-active.d must exist for the traversal to resolve at
        all: `[ -f ]` on a path whose intermediate directory component is
        missing is false regardless of the guard, which would make this test
        pass vacuously in both states.

        (The sibling gate require-routing-read.sh is genuinely
        non-discriminable under the same collapse because it only reads. Do
        not carry that reasoning to a hook that writes.)"""
        (isolated_home / ".claude" / ".plan-review-active.d").mkdir(parents=True, exist_ok=True)
        canary = plant_traversal_canary(isolated_home)
        mtime_before = canary.stat().st_mtime_ns

        assert run_hook(
            LOG_ROUTING_READ_HOOK,
            read_input(ROUTING_MD_PATH, session_id=TRAVERSAL_SESSION_ID),
        ) == "allow"
        assert canary.stat().st_mtime_ns == mtime_before, (
            "a traversal session_id must not reach the touch on a file "
            "outside .plan-review-routing-read.d/"
        )
        assert canary.read_text() == CANARY_CONTENT

    def test_traversal_session_id_does_not_touch_pending_read_marker_dir(self, isolated_home):
        """The pending-read write has no active-marker precondition (unlike
        the routing-read write above), so this needs its own traversal proof
        independent of test_traversal_session_id_allows_and_does_not_touch_marker_dir
        — a session_id of '../canary' must not reach it either.

        .plan-review-pending-read.d/../canary resolves to the same
        $HOME/.claude/canary the sibling test above uses; see that test's
        docstring for why mtime, not content, is the assertion that
        discriminates guard-present from guard-absent for a `touch`."""
        canary = plant_traversal_canary(isolated_home)
        mtime_before = canary.stat().st_mtime_ns

        assert run_hook(
            LOG_ROUTING_READ_HOOK,
            read_input(ROUTING_MD_PATH, session_id=TRAVERSAL_SESSION_ID),
        ) == "allow"
        assert canary.stat().st_mtime_ns == mtime_before, (
            "a traversal session_id must not reach the touch on a file "
            "outside .plan-review-pending-read.d/"
        )
        assert canary.read_text() == CANARY_CONTENT
