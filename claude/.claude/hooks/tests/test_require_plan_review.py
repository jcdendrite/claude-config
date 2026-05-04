"""Tests for require-plan-review.sh."""
from __future__ import annotations

import os
import subprocess
import time

import pytest
from helpers import (
    HOOKS_DIR,
    SKILLS_DIR,
    bash_input,
    edit_input,
    extract_skill_command,
    multiedit_input,
    plan_review_marker_path,
    run_hook,
    run_hook_reason,
    run_skill_command,
    write_input,
    write_plan_review_marker,
)

PLAN_REVIEW_SKILL = SKILLS_DIR / "plan-review" / "SKILL.md"

REQUIRE_PLAN_REVIEW_HOOK = HOOKS_DIR / "require-plan-review.sh"


@pytest.fixture
def plan_review_repo(tmp_path):
    """Git repo with .claude/plans/ populated.

    The gate is globally applied — no opt-in required.
    """
    repo = tmp_path / "plan-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    plans_dir = repo / ".claude" / "plans"
    plans_dir.mkdir(parents=True)
    (plans_dir / "impl-plan.md").write_text("# Implementation plan\n\nStep 1...\n")
    return repo


@pytest.fixture
def plan_review_home(isolated_home):
    """Isolated $HOME with the plan-review-markers directory pre-created."""
    (isolated_home / ".claude" / "plan-review-markers").mkdir(parents=True, exist_ok=True)
    return isolated_home


class TestRequirePlanReview:
    def test_plan_exists_no_marker_denies_write(self, plan_review_repo, plan_review_home):
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input("/tmp/foo.py"), "session_id": "test-session-prt"},
                cwd=plan_review_repo,
            )
            == "deny"
        )

    def test_plan_exists_no_marker_denies_edit(self, plan_review_repo, plan_review_home):
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**edit_input("/tmp/foo.py"), "session_id": "test-session-prt"},
                cwd=plan_review_repo,
            )
            == "deny"
        )

    def test_plan_exists_with_marker_allows_write(self, plan_review_repo, plan_review_home):
        sid = "test-session-prt-allowed"
        write_plan_review_marker(plan_review_home, plan_review_repo, sid)
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input("/tmp/foo.py"), "session_id": sid},
                cwd=plan_review_repo,
            )
            == "allow"
        )

    def test_plan_exists_with_marker_allows_edit(self, plan_review_repo, plan_review_home):
        sid = "test-session-prt-allowed-edit"
        write_plan_review_marker(plan_review_home, plan_review_repo, sid)
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**edit_input("/tmp/foo.py"), "session_id": sid},
                cwd=plan_review_repo,
            )
            == "allow"
        )

    def test_other_sessions_marker_does_not_authorize(self, plan_review_repo, plan_review_home):
        """Marker for session A must NOT bypass session B's gate."""
        write_plan_review_marker(plan_review_home, plan_review_repo, "session-A")
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input("/tmp/foo.py"), "session_id": "session-B"},
                cwd=plan_review_repo,
            )
            == "deny"
        )

    def test_no_plans_dir_allows(self, tmp_path):
        """No .claude/plans/ directory → gate is inactive."""
        repo = tmp_path / "no-plans"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                write_input("/tmp/foo.py"),
                cwd=repo,
            )
            == "allow"
        )

    def test_empty_plans_dir_allows(self, tmp_path):
        """Empty .claude/plans/ directory → no plans present, gate inactive."""
        repo = tmp_path / "empty-plans"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        (repo / ".claude" / "plans").mkdir(parents=True)
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                write_input("/tmp/foo.py"),
                cwd=repo,
            )
            == "allow"
        )

    def test_bash_tool_allows_always(self, plan_review_repo):
        """Bash tool calls are not gated — only Write and Edit."""
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                bash_input("git commit -m foo"),
                cwd=plan_review_repo,
            )
            == "allow"
        )

    def test_outside_git_repo_allows(self, tmp_path):
        """Outside a git repo, the hook cannot key a marker — allow through."""
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                write_input("/tmp/foo.py"),
                cwd=non_repo,
            )
            == "allow"
        )

    def test_no_session_id_in_input_denies(self, plan_review_repo, plan_review_home):
        """Without session_id in the hook payload, no per-session marker can be
        keyed — deny even if a marker directory exists. Mirrors the same invariant
        in require-code-review.sh and require-respond-pr.sh: missing session_id
        must fail-closed, not silently allow. This is a load-bearing safety
        property of the per-session marker design."""
        # Write a marker for a known session so the marker dir exists, but the
        # payload has no session_id — the hook must not accept the existing marker.
        write_plan_review_marker(plan_review_home, plan_review_repo, "some-other-session")
        # write_input() uses no session_id field.
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                write_input("/tmp/foo.py"),
                cwd=plan_review_repo,
            )
            == "deny"
        )

    def test_deny_message_mentions_plan_review(self, plan_review_repo, plan_review_home):
        """Deny reason must reference /plan-review so the agent knows what to run."""
        reason = run_hook_reason(
            REQUIRE_PLAN_REVIEW_HOOK,
            {**write_input("/tmp/foo.py"), "session_id": "session-for-reason"},
            cwd=plan_review_repo,
        )
        assert reason is not None
        assert "/plan-review" in reason
        assert "plan-review-markers" in reason

    # -- Active-marker bypass -----------------------------------------------
    # Marker layout: ~/.claude/.plan-review-active.d/<session_id>.
    # The /plan-review skill writes one file per session at Step 0 and
    # removes it at the deactivation step. While fresh (<60 min), the hook
    # bypasses the gate so the skill's own Write/Edit calls don't self-deny.

    def test_fresh_active_marker_allows_write(self, plan_review_repo, plan_review_home):
        """Active marker created by /plan-review Step 0 bypasses the gate."""
        sid = "session-active-write"
        marker_dir = plan_review_home / ".claude" / ".plan-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).touch()
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input("/tmp/foo.py"), "session_id": sid},
                cwd=plan_review_repo,
            )
            == "allow"
        )

    def test_fresh_active_marker_allows_edit(self, plan_review_repo, plan_review_home):
        sid = "session-active-edit"
        marker_dir = plan_review_home / ".claude" / ".plan-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).touch()
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**edit_input("/tmp/foo.py"), "session_id": sid},
                cwd=plan_review_repo,
            )
            == "allow"
        )

    def test_fresh_active_marker_allows_multiedit(self, plan_review_repo, plan_review_home):
        sid = "session-active-multiedit"
        marker_dir = plan_review_home / ".claude" / ".plan-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).touch()
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**multiedit_input("/tmp/foo.py"), "session_id": sid},
                cwd=plan_review_repo,
            )
            == "allow"
        )

    def test_plan_exists_no_marker_denies_multiedit(
        self, plan_review_repo, plan_review_home
    ):
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**multiedit_input("/tmp/foo.py"), "session_id": "test-session-prt"},
                cwd=plan_review_repo,
            )
            == "deny"
        )

    def test_stale_active_marker_falls_through_to_deny(
        self, plan_review_repo, plan_review_home
    ):
        """>60min old active marker doesn't bypass; no completion marker → deny."""
        sid = "session-stale-active"
        marker_dir = plan_review_home / ".claude" / ".plan-review-active.d"
        marker_dir.mkdir(parents=True)
        marker = marker_dir / sid
        marker.touch()
        ninety_min_ago = time.time() - 90 * 60
        os.utime(marker, (ninety_min_ago, ninety_min_ago))
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input("/tmp/foo.py"), "session_id": sid},
                cwd=plan_review_repo,
            )
            == "deny"
        )

    def test_other_sessions_active_marker_does_not_bypass(
        self, plan_review_repo, plan_review_home
    ):
        """Per-session keying: session A's active marker must NOT bypass session B."""
        marker_dir = plan_review_home / ".claude" / ".plan-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / "session-A").touch()
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input("/tmp/foo.py"), "session_id": "session-B"},
                cwd=plan_review_repo,
            )
            == "deny"
        )

    def test_active_marker_mtime_refreshed_on_bypass(
        self, plan_review_repo, plan_review_home
    ):
        """Long-running review mitigation: hook touches the active marker on each
        bypass so a session approaching the 60-min cutoff doesn't get blocked."""
        sid = "session-long-review"
        marker_dir = plan_review_home / ".claude" / ".plan-review-active.d"
        marker_dir.mkdir(parents=True)
        marker = marker_dir / sid
        marker.touch()
        fifty_min_ago = time.time() - 50 * 60
        os.utime(marker, (fifty_min_ago, fifty_min_ago))
        pre_mtime = marker.stat().st_mtime
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input("/tmp/foo.py"), "session_id": sid},
                cwd=plan_review_repo,
            )
            == "allow"
        )
        assert marker.stat().st_mtime > pre_mtime, (
            "active marker mtime must be refreshed on bypass to keep long reviews alive"
        )

    # -- SKILL.md fixture alignment -----------------------------------------

    def test_skill_activate_command_creates_bypass_marker(
        self, plan_review_repo, plan_review_home
    ):
        """Run the SKILL.md activate-gate recipe; verify the resulting marker
        authorizes a previously-gated Write."""
        sid = "session-skill-activate"
        sessions_dir = plan_review_home / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / str(os.getpid())).write_text(sid)

        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input("/tmp/foo.py"), "session_id": sid},
                cwd=plan_review_repo,
            )
            == "deny"
        ), "precondition: Write must be gated before activation"

        run_skill_command(
            extract_skill_command(PLAN_REVIEW_SKILL, "activate-gate"),
            cwd=plan_review_repo,
            isolated_home=plan_review_home,
        )

        marker = plan_review_home / ".claude" / ".plan-review-active.d" / sid
        assert marker.exists(), (
            "SKILL.md activate-gate recipe ran but no marker landed at the path "
            "the hook checks — skill and hook disagree on layout."
        )
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input("/tmp/foo.py"), "session_id": sid},
                cwd=plan_review_repo,
            )
            == "allow"
        )

    def test_skill_deactivate_command_removes_bypass_marker(
        self, plan_review_repo, plan_review_home
    ):
        """Run activate then deactivate from SKILL.md; verify deactivate removes
        the marker and the hook re-gates."""
        sid = "session-skill-deactivate"
        sessions_dir = plan_review_home / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / str(os.getpid())).write_text(sid)

        run_skill_command(
            extract_skill_command(PLAN_REVIEW_SKILL, "activate-gate"),
            cwd=plan_review_repo,
            isolated_home=plan_review_home,
        )
        marker = plan_review_home / ".claude" / ".plan-review-active.d" / sid
        assert marker.exists(), "activate-gate setup did not create the marker"

        run_skill_command(
            extract_skill_command(PLAN_REVIEW_SKILL, "deactivate-gate"),
            cwd=plan_review_repo,
            isolated_home=plan_review_home,
        )
        assert not marker.exists(), (
            "SKILL.md deactivate-gate recipe ran but marker is still present — "
            "skill and hook disagree on the marker path."
        )
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input("/tmp/foo.py"), "session_id": sid},
                cwd=plan_review_repo,
            )
            == "deny"
        )

    def test_skill_completion_command_writes_completion_marker(
        self, plan_review_repo, plan_review_home
    ):
        """Run the SKILL.md record-completion recipe; verify the resulting marker
        authorizes a previously-gated Write via the completion path."""
        sid = "session-skill-completion"
        sessions_dir = plan_review_home / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / str(os.getpid())).write_text(sid)

        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input("/tmp/foo.py"), "session_id": sid},
                cwd=plan_review_repo,
            )
            == "deny"
        ), "precondition: Write must be gated before completion marker is written"

        run_skill_command(
            extract_skill_command(PLAN_REVIEW_SKILL, "record-completion"),
            cwd=plan_review_repo,
            isolated_home=plan_review_home,
        )

        expected_marker = plan_review_marker_path(plan_review_home, plan_review_repo, sid)
        assert expected_marker.exists(), (
            "SKILL.md record-completion recipe ran but no marker landed at the path "
            "the hook checks — skill and hook disagree on layout."
        )
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input("/tmp/foo.py"), "session_id": sid},
                cwd=plan_review_repo,
            )
            == "allow"
        )
