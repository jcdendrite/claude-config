"""Tests for require-plan-review.sh."""
from __future__ import annotations

import os
import subprocess

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
                {**write_input(str(plan_review_repo / "src" / "foo.py")), "session_id": "test-session-prt"},
                cwd=plan_review_repo,
            )
            == "deny"
        )

    def test_plan_exists_no_marker_denies_edit(self, plan_review_repo, plan_review_home):
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**edit_input(str(plan_review_repo / "src" / "foo.py")), "session_id": "test-session-prt"},
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
                {**write_input(str(plan_review_repo / "src" / "foo.py")), "session_id": "session-B"},
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
                write_input(str(plan_review_repo / "src" / "foo.py")),
                cwd=plan_review_repo,
            )
            == "deny"
        )

    def test_missing_file_path_denies(self, plan_review_repo, plan_review_home):
        """Missing file_path in the hook payload cannot be resolved — fail-closed
        and deny. Parallel to test_no_session_id_in_input_denies: security controls
        must not silently allow on parse failure."""
        payload = {"tool_name": "Write", "tool_input": {}, "session_id": "session-no-path"}
        assert (
            run_hook(REQUIRE_PLAN_REVIEW_HOOK, payload, cwd=plan_review_repo) == "deny"
        )

    def test_deny_message_mentions_plan_review(self, plan_review_repo, plan_review_home):
        """Deny reason must reference /plan-review so the agent knows what to run."""
        reason = run_hook_reason(
            REQUIRE_PLAN_REVIEW_HOOK,
            {**write_input(str(plan_review_repo / "src" / "foo.py")), "session_id": "session-for-reason"},
            cwd=plan_review_repo,
        )
        assert reason is not None
        assert "/plan-review" in reason
        assert "plan-review-markers" in reason

    # -- Committed-clean plan suppression -----------------------------------
    # A plan file that is tracked and unmodified vs HEAD is historical —
    # its review shipped with the PR that committed it. It must not re-arm
    # the gate in a new session. Only untracked or modified plan files count
    # as active work.

    def test_committed_clean_plan_does_not_arm_gate(self, plan_review_repo, plan_review_home):
        """A plan file that is committed and unmodified vs HEAD does not arm the gate."""
        subprocess.run(["git", "add", ".claude/plans/impl-plan.md"], cwd=plan_review_repo, check=True)
        subprocess.run(["git", "commit", "-m", "plan"], cwd=plan_review_repo, check=True)
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input(str(plan_review_repo / "src" / "foo.py")), "session_id": "session-committed-clean"},
                cwd=plan_review_repo,
            )
            == "allow"
        )

    def test_modified_committed_plan_arms_gate(self, plan_review_repo, plan_review_home):
        """A plan file that is committed but modified on disk still arms the gate."""
        subprocess.run(["git", "add", ".claude/plans/impl-plan.md"], cwd=plan_review_repo, check=True)
        subprocess.run(["git", "commit", "-m", "plan"], cwd=plan_review_repo, check=True)
        (plan_review_repo / ".claude" / "plans" / "impl-plan.md").write_text(
            "# Implementation plan\n\nStep 1...\n\nStep 2 (added)...\n"
        )
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input(str(plan_review_repo / "src" / "foo.py")), "session_id": "session-modified-committed"},
                cwd=plan_review_repo,
            )
            == "deny"
        )

    def test_one_committed_one_uncommitted_plan_arms_gate(self, plan_review_repo, plan_review_home):
        """If any plan file is untracked, the gate arms even if another is committed-clean."""
        subprocess.run(["git", "add", ".claude/plans/impl-plan.md"], cwd=plan_review_repo, check=True)
        subprocess.run(["git", "commit", "-m", "plan"], cwd=plan_review_repo, check=True)
        (plan_review_repo / ".claude" / "plans" / "new-plan.md").write_text("# New plan\n")
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input(str(plan_review_repo / "src" / "foo.py")), "session_id": "session-one-committed-one-not"},
                cwd=plan_review_repo,
            )
            == "deny"
        )

    # -- Active-marker bypass -----------------------------------------------
    # Marker layout: ~/.claude/.plan-review-active.d/<session_id>.
    # The /plan-review skill writes one file per session at Step 0 and
    # removes it at the deactivation step. While fresh (<60 min), the hook
    # bypasses the gate so the skill's own Write/Edit calls don't self-deny.

    def test_fresh_active_marker_allows_write(self, plan_review_repo, plan_review_home):
        """Active marker with alive PID bypasses the gate for Write tool calls."""
        sid = "session-active-write"
        marker_dir = plan_review_home / ".claude" / ".plan-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).write_text(str(os.getpid()))
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
        (marker_dir / sid).write_text(str(os.getpid()))
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
        (marker_dir / sid).write_text(str(os.getpid()))
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
                {**multiedit_input(str(plan_review_repo / "src" / "foo.py")), "session_id": "test-session-prt"},
                cwd=plan_review_repo,
            )
            == "deny"
        )

    def test_alive_pid_active_marker_bypasses(
        self, plan_review_repo, plan_review_home
    ):
        """Active marker whose stored PID is alive bypasses the gate."""
        sid = "session-alive-pid"
        marker_dir = plan_review_home / ".claude" / ".plan-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).write_text(str(os.getpid()))
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input(str(plan_review_repo / "src" / "foo.py")), "session_id": sid},
                cwd=plan_review_repo,
            )
            == "allow"
        )

    def test_dead_pid_active_marker_evicts_and_denies(
        self, plan_review_repo, plan_review_home
    ):
        """Active marker whose stored PID is dead is evicted; gate denies."""
        sid = "session-dead-pid"
        marker_dir = plan_review_home / ".claude" / ".plan-review-active.d"
        marker_dir.mkdir(parents=True)
        marker = marker_dir / sid
        marker.write_text("99999999")  # PID outside Linux/macOS max range → always dead
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input(str(plan_review_repo / "src" / "foo.py")), "session_id": sid},
                cwd=plan_review_repo,
            )
            == "deny"
        )
        assert not marker.exists(), "hook must evict the orphan marker on dead PID"

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
                {**write_input(str(plan_review_repo / "src" / "foo.py")), "session_id": "session-B"},
                cwd=plan_review_repo,
            )
            == "deny"
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
                {**write_input(str(plan_review_repo / "src" / "foo.py")), "session_id": sid},
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
                {**write_input(str(plan_review_repo / "src" / "foo.py")), "session_id": sid},
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
                {**write_input(str(plan_review_repo / "src" / "foo.py")), "session_id": sid},
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

    # -- Write-target scope filter ------------------------------------------
    # The gate applies only to writes inside the current repo's working tree.
    # User-home and other out-of-repo writes pass through so that plan-mode
    # can author its scratch plan file at ~/.claude/plans/<slug>.md without
    # needing a bypass marker.

    def test_home_dir_plan_write_passes_through(
        self, plan_review_repo, plan_review_home
    ):
        """Write to ~/.claude/plans/ passes through even when gate is armed.
        Mirrors the plan-mode case where the harness directs the plan file to
        ~/.claude/plans/<session-slug>.md."""
        home_plan_path = str(plan_review_home / ".claude" / "plans" / "session-plan.md")
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input(home_plan_path), "session_id": "session-scope-home"},
                cwd=plan_review_repo,
            )
            == "allow"
        )

    def test_outside_repo_write_passes_through(self, plan_review_repo, plan_review_home):
        """Write to an arbitrary out-of-repo path passes through when gate is armed."""
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input("/tmp/scratch/foo.py"), "session_id": "session-scope-tmp"},
                cwd=plan_review_repo,
            )
            == "allow"
        )

    def test_in_repo_write_still_denies(self, plan_review_repo, plan_review_home):
        """Write to a path inside the repo is still denied when gate is armed and
        no marker exists — positive control for the scope filter."""
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input(str(plan_review_repo / "src" / "main.py")), "session_id": "session-scope-deny"},
                cwd=plan_review_repo,
            )
            == "deny"
        )

    def test_sibling_repo_path_not_treated_as_in_repo(
        self, plan_review_repo, plan_review_home, tmp_path
    ):
        """A path whose prefix shares the repo root name but with extra characters
        (e.g. /tmp/x/plan-repo-sibling/...) must not be mistaken for an in-repo
        path. Guards the trailing-slash prefix match."""
        sibling_path = str(tmp_path / (plan_review_repo.name + "-sibling") / "foo.py")
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input(sibling_path), "session_id": "session-scope-sibling"},
                cwd=plan_review_repo,
            )
            == "allow"
        )
