"""Tests for require-plan-review.sh."""
from __future__ import annotations

import subprocess

import pytest
from helpers import (
    HOOKS_DIR,
    bash_input,
    edit_input,
    run_hook,
    run_hook_reason,
    write_input,
    write_plan_review_marker,
)

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
