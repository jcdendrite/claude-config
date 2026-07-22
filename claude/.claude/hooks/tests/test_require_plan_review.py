"""Tests for require-plan-review.sh."""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest
from helpers import (
    CLAUDE_DIR,
    HOOKS_DIR,
    SCRIPTS_DIR,
    SKILLS_DIR,
    bash_input,
    edit_input,
    exitplanmode_input,
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
        """Target must be in-repo: an out-of-repo path like /tmp/foo.py would
        also allow via the scope filter's out-of-repo exemption regardless of
        whether the marker's hash matched, masking the marker check."""
        sid = "test-session-prt-allowed"
        write_plan_review_marker(plan_review_home, plan_review_repo, sid)
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input(str(plan_review_repo / "src" / "foo.py")), "session_id": sid},
                cwd=plan_review_repo,
            )
            == "allow"
        )

    def test_plan_exists_with_marker_allows_edit(self, plan_review_repo, plan_review_home):
        """In-repo target for the same reason as the Write case above."""
        sid = "test-session-prt-allowed-edit"
        write_plan_review_marker(plan_review_home, plan_review_repo, sid)
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**edit_input(str(plan_review_repo / "src" / "foo.py")), "session_id": sid},
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

    def test_agent_reviews_path_allows_without_marker(self, plan_review_repo, plan_review_home):
        """Writes to agent-reviews/ are exempt from the plan-review gate.

        Reviewer agents write findings to agent-reviews/ while a plan is in flight
        (e.g. code-review runs after plan-it). Blocking them forces a full-inline
        fallback that defeats the context-saving canary. The exemption is scoped to
        an exact prefix match — only paths under <repo>/agent-reviews/ pass through.
        """
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {
                    **write_input(str(plan_review_repo / "agent-reviews" / "staff-backend-engineer-123.md")),
                    "session_id": "test-session-canary-exempt",
                },
                cwd=plan_review_repo,
            )
            == "allow"
        )

    def test_adjacent_path_not_exempt(self, plan_review_repo, plan_review_home):
        """Paths adjacent to agent-reviews/ are NOT exempt — only exact prefix match.

        The exemption comment states: "Exact prefix match only: foo-agent-reviews/
        does not satisfy this." Verify that invariant holds — a sibling directory
        whose name merely contains 'agent-reviews' still requires a plan-review marker.
        """
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {
                    **write_input(str(plan_review_repo / "foo-agent-reviews" / "test.md")),
                    "session_id": "test-session-adjacent-deny",
                },
                cwd=plan_review_repo,
            )
            == "deny"
        )

    def test_agent_reviews_nested_path_not_exempt(self, plan_review_repo, plan_review_home):
        """A path with agent-reviews/ as a non-root-level component is NOT exempt.

        The exemption pattern is `<repo>/agent-reviews/*` — only paths whose first
        component after the repo root is literally `agent-reviews` match. A path like
        `<repo>/src/agent-reviews/file.md` does not match, preventing a glob-widening
        refactor from accidentally exempting arbitrary deep paths.
        """
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {
                    **write_input(str(plan_review_repo / "src" / "agent-reviews" / "file.md")),
                    "session_id": "test-session-nested-deny",
                },
                cwd=plan_review_repo,
            )
            == "deny"
        )

    def test_agent_reviews_directory_itself_not_exempt(self, plan_review_repo, plan_review_home):
        """A write to the agent-reviews/ directory itself (no filename) is NOT exempt.

        The hook glob pattern is `agent-reviews/*` — a bare directory path with no
        trailing filename component does not match. The write falls through to the
        in-repo boundary check and is denied. This pins the exemption to file paths
        *under* the directory, not the directory path itself, guarding against a
        future glob-widening refactor that might accidentally widen the exemption.
        """
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {
                    **write_input(str(plan_review_repo / "agent-reviews")),
                    "session_id": "test-session-dir-itself-deny",
                },
                cwd=plan_review_repo,
            )
            == "deny"
        )

    def test_agent_reviews_path_edit_allows_without_marker(self, plan_review_repo, plan_review_home):
        """Edit tool writes to agent-reviews/ are also exempt, not just Write.

        The exemption fires before the Write/Edit/MultiEdit gate — it applies to all
        three tool types equally. This test pins the exemption against a refactor that
        moves the check inside the Write-only branch.
        """
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {
                    **edit_input(str(plan_review_repo / "agent-reviews" / "staff-sdet-123.md")),
                    "session_id": "test-session-canary-exempt-edit",
                },
                cwd=plan_review_repo,
            )
            == "allow"
        )

    def test_agent_reviews_path_multiedit_allows_without_marker(self, plan_review_repo, plan_review_home):
        """MultiEdit tool writes to agent-reviews/ are also exempt, not just Write.

        Mirrors test_agent_reviews_path_edit_allows_without_marker for the MultiEdit
        tool variant, completing the three-way Write/Edit/MultiEdit coverage established
        by the rest of this test suite.
        """
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {
                    **multiedit_input(str(plan_review_repo / "agent-reviews" / "ciso-reviewer-456.md")),
                    "session_id": "test-session-canary-exempt-multiedit",
                },
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
        """Missing file_path leaves TARGET_PATH empty, causing the scope-filter guard
        (`if [ -n "$TARGET_PATH" ]`) to be false — the hook skips both the
        agent-reviews/ exemption and the out-of-repo pass-through and falls through
        to the default emit_deny. The scope filter only ever adds allows (narrows the
        deny set), so skipping it when the path is unparseable can never wrongly
        allow — an empty or missing path fails closed. Parallel to
        test_no_session_id_in_input_denies: security controls must not silently allow
        on parse failure."""
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


class TestMarkerWriteReadAgreement:
    """The write side (marker.sh) and the read side (require-plan-review.sh)
    must agree byte-for-byte on the active-plan hash. Every other test seeds
    the marker through a Python helper, so each side is only ever checked
    against a stand-in for the other -- a divergence in how marker.sh
    resolves the repo root or writes the value would be invisible."""

    def _seed_session(self, home, sid):
        sessions_dir = home / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        (sessions_dir / str(os.getpid())).write_text(sid)

    def _write_marker_via_script(self, repo, home):
        return subprocess.run(
            ["bash", str(SCRIPTS_DIR / "marker.sh"), "write", "plan-review"],
            cwd=repo,
            env={**os.environ, "HOME": str(home)},
            capture_output=True,
            text=True,
        )

    def test_real_marker_write_allows_then_denies_after_plan_edit(
        self, plan_review_repo, plan_review_home
    ):
        sid = "session-e2e-write-read"
        self._seed_session(plan_review_home, sid)

        result = self._write_marker_via_script(plan_review_repo, plan_review_home)
        assert result.returncode == 0, result.stderr

        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**exitplanmode_input(), "session_id": sid},
                cwd=plan_review_repo,
            )
            == "allow"
        ), "a marker written by the real marker.sh must satisfy the real hook"

        (plan_review_repo / ".claude" / "plans" / "impl-plan.md").write_text(
            "# Implementation plan\n\nStep 1...\n\nRevised after review.\n"
        )
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**exitplanmode_input(), "session_id": sid},
                cwd=plan_review_repo,
            )
            == "deny"
        ), "editing the plan after a real marker write must re-arm the gate"


class TestUnhashablePlanFailsClosed:
    """An active plan that cannot be hashed is an UNKNOWN review state, not
    an absent one. _lib_active_plan_hash returns empty for "no plan active"
    and exits non-zero for "plan active but unhashable"; collapsing those
    two onto the same empty-string signal made a transient unreadable plan
    file silently disarm the gate (fail-open)."""

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_plan_denies_write(self, plan_review_repo, plan_review_home):
        sid = "session-unhashable-write"
        write_plan_review_marker(plan_review_home, plan_review_repo, sid)
        plan = plan_review_repo / ".claude" / "plans" / "impl-plan.md"
        plan.chmod(0o000)
        try:
            decision = run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input(str(plan_review_repo / "src" / "foo.py")), "session_id": sid},
                cwd=plan_review_repo,
            )
        finally:
            plan.chmod(0o644)
        assert decision == "deny"

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_plan_denies_exitplanmode(self, plan_review_repo, plan_review_home):
        """ExitPlanMode carries no file_path, so it skips the repo-scope
        filter -- the hash path is the only thing deciding allow/deny."""
        sid = "session-unhashable-epm"
        write_plan_review_marker(plan_review_home, plan_review_repo, sid)
        plan = plan_review_repo / ".claude" / "plans" / "impl-plan.md"
        plan.chmod(0o000)
        try:
            decision = run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**exitplanmode_input(), "session_id": sid},
                cwd=plan_review_repo,
            )
        finally:
            plan.chmod(0o644)
        assert decision == "deny"

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_plan_deny_names_file_and_repair_route(
        self, plan_review_repo, plan_review_home
    ):
        """The remedy must be actionable. Telling the user to run
        /plan-review here would be circular -- marker.sh hits the identical
        condition and aborts -- so the message names the offending file and
        points at Bash, which this hook does not gate."""
        sid = "session-unhashable-reason"
        plan = plan_review_repo / ".claude" / "plans" / "impl-plan.md"
        plan.chmod(0o000)
        try:
            reason = run_hook_reason(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**exitplanmode_input(), "session_id": sid},
                cwd=plan_review_repo,
            )
        finally:
            plan.chmod(0o644)
        assert reason is not None, "an unhashable active plan must deny, not allow"
        assert "impl-plan.md" in reason, (
            f"deny reason must name the unreadable plan file, got {reason!r}"
        )
        assert "chmod" in reason, (
            f"deny reason must name a concrete repair command, got {reason!r}"
        )


class TestContentAddressedPlanReviewMarker:
    """The completion marker stores a content hash of the active plan file
    set (GH #466), not an existence-only sentinel -- editing a reviewed plan
    must re-arm the gate on the next Write/Edit/ExitPlanMode."""

    def test_stale_hash_denies_write_after_plan_edit(self, plan_review_repo, plan_review_home):
        """Editing the plan after a clean review changes the active-plan
        hash, so the stored marker no longer matches -- the gate re-arms."""
        sid = "session-hash-stale-write"
        write_plan_review_marker(plan_review_home, plan_review_repo, sid)
        (plan_review_repo / ".claude" / "plans" / "impl-plan.md").write_text(
            "# Implementation plan\n\nStep 1...\n\nRevised.\n"
        )
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input(str(plan_review_repo / "src" / "foo.py")), "session_id": sid},
                cwd=plan_review_repo,
            )
            == "deny"
        )

    def test_stale_hash_denies_exitplanmode_after_plan_edit(self, plan_review_repo, plan_review_home):
        """Same as the Write case, but for ExitPlanMode -- the tool that has
        no file_path and so skips the scope filter entirely, making the
        marker hash comparison the only thing standing between allow/deny."""
        sid = "session-hash-stale-epm"
        write_plan_review_marker(plan_review_home, plan_review_repo, sid)
        (plan_review_repo / ".claude" / "plans" / "impl-plan.md").write_text(
            "# Implementation plan\n\nStep 1...\n\nRevised for ExitPlanMode.\n"
        )
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**exitplanmode_input(), "session_id": sid},
                cwd=plan_review_repo,
            )
            == "deny"
        )

    def test_committed_clean_plan_does_not_contribute_to_hash(self, plan_review_repo, plan_review_home):
        """One committed-clean plan + one active plan: the gate is armed;
        committing the previously-active plan (so both are now historical)
        disarms it entirely -- proving the committed-clean file contributes
        nothing to the hash. A break-on-first-find-result bug that hashed
        every enumerated file regardless of active/historical status would
        leave the gate armed forever here."""
        subprocess.run(["git", "add", ".claude/plans/impl-plan.md"], cwd=plan_review_repo, check=True)
        subprocess.run(["git", "commit", "-m", "plan"], cwd=plan_review_repo, check=True)
        active_plan = plan_review_repo / ".claude" / "plans" / "new-plan.md"
        active_plan.write_text("# New plan\n")

        sid = "session-armset-agreement"
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input(str(plan_review_repo / "src" / "foo.py")), "session_id": sid},
                cwd=plan_review_repo,
            )
            == "deny"
        ), "precondition: the untracked active plan must arm the gate"

        subprocess.run(["git", "add", ".claude/plans/new-plan.md"], cwd=plan_review_repo, check=True)
        subprocess.run(["git", "commit", "-m", "new plan"], cwd=plan_review_repo, check=True)
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input(str(plan_review_repo / "src" / "foo.py")), "session_id": sid},
                cwd=plan_review_repo,
            )
            == "allow"
        ), "committing the previously-active plan should disarm the gate entirely"

    def test_multiplan_active_set_rearms_on_either_edit(self, plan_review_repo, plan_review_home):
        """Two active (untracked) plans: a marker written over both allows;
        editing EITHER one afterward denies -- guards against a ported
        break-on-first-plan bug that only hashed the first enumerated file."""
        second_plan = plan_review_repo / ".claude" / "plans" / "second-plan.md"
        second_plan.write_text("# Second plan\n")
        first_plan = plan_review_repo / ".claude" / "plans" / "impl-plan.md"

        sid = "session-multiplan"
        write_plan_review_marker(plan_review_home, plan_review_repo, sid)
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input(str(plan_review_repo / "src" / "foo.py")), "session_id": sid},
                cwd=plan_review_repo,
            )
            == "allow"
        ), "precondition: marker over the two-plan active set must allow"

        # Editing the SECOND enumerated plan (alphabetically after the first)
        # is exactly what a break-on-first-find-result bug would miss.
        second_plan.write_text("# Second plan (edited)\n")
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input(str(plan_review_repo / "src" / "foo.py")), "session_id": sid},
                cwd=plan_review_repo,
            )
            == "deny"
        ), "editing the second active plan must re-arm the gate"

        # Reset with a fresh review, then edit the FIRST plan instead.
        second_plan.write_text("# Second plan\n")
        write_plan_review_marker(plan_review_home, plan_review_repo, sid)
        first_plan.write_text("# Implementation plan\n\nStep 1 (edited)...\n")
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input(str(plan_review_repo / "src" / "foo.py")), "session_id": sid},
                cwd=plan_review_repo,
            )
            == "deny"
        ), "editing the first active plan must also re-arm the gate"

    def test_legacy_literal_marker_denies_cleanly(self, plan_review_repo, plan_review_home):
        """A pre-migration marker containing the literal 'reviewed' sentinel
        (written by the old existence-only code) must deny cleanly against an
        active plan under the new hash-compare logic -- fail-closed, not an
        error under pipefail."""
        sid = "session-legacy-marker"
        marker = plan_review_marker_path(plan_review_home, plan_review_repo, sid)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("reviewed\n")
        assert (
            run_hook(
                REQUIRE_PLAN_REVIEW_HOOK,
                {**write_input(str(plan_review_repo / "src" / "foo.py")), "session_id": sid},
                cwd=plan_review_repo,
            )
            == "deny"
        )


class TestRequirePlanReviewExitPlanMode:
    """Gate behavior for the ExitPlanMode tool.

    ExitPlanMode is the harness tool that presents the completed plan to the
    user for approval. It must be gated by the same require-plan-review.sh
    hook that gates Write/Edit, but with two important differences:
      1. No file_path field — the path-scope filter is skipped (TARGET_PATH
         empty → `if [ -n "$TARGET_PATH" ]` is false → falls through to deny).
      2. Active-marker bypass does NOT apply — an active marker means
         plan-review is in progress but not yet complete; ExitPlanMode must
         remain blocked until review is finished and a completion marker exists.
    """

    def test_plan_exists_no_marker_denies_exitplanmode(self, plan_review_repo, plan_review_home):
        """Gate arms: plan exists, no marker → ExitPlanMode denied."""
        result = run_hook(
            REQUIRE_PLAN_REVIEW_HOOK,
            {**exitplanmode_input(), "session_id": "test-session-epm"},
            cwd=plan_review_repo,
        )
        assert result == "deny"

    def test_plan_exists_no_marker_exitplanmode_deny_reason(self, plan_review_repo, plan_review_home):
        """Deny reason contains /plan-review reference and ExitPlanMode-specific wording."""
        reason = run_hook_reason(
            REQUIRE_PLAN_REVIEW_HOOK,
            {**exitplanmode_input(), "session_id": "test-session-epm-reason"},
            cwd=plan_review_repo,
        )
        assert reason is not None
        assert "/plan-review" in reason
        assert "ExitPlanMode" in reason and "plan presentation" in reason.lower()

    def test_completion_marker_allows_exitplanmode(self, plan_review_repo, plan_review_home):
        """Completion marker present → ExitPlanMode allowed (happy path)."""
        sid = "test-session-epm-allow"
        write_plan_review_marker(plan_review_home, plan_review_repo, sid)
        result = run_hook(
            REQUIRE_PLAN_REVIEW_HOOK,
            {**exitplanmode_input(), "session_id": sid},
            cwd=plan_review_repo,
        )
        assert result == "allow"

    def test_no_plan_file_allows_exitplanmode(self, plan_review_repo, plan_review_home):
        """No plan file in .claude/plans/ → ExitPlanMode allowed (gate inactive)."""
        plans_dir = plan_review_repo / ".claude" / "plans"
        shutil.rmtree(plans_dir, ignore_errors=True)
        result = run_hook(
            REQUIRE_PLAN_REVIEW_HOOK,
            {**exitplanmode_input(), "session_id": "test-session-epm-noplan"},
            cwd=plan_review_repo,
        )
        assert result == "allow"

    def test_committed_clean_plan_allows_exitplanmode(self, plan_review_repo, plan_review_home):
        """Committed, unmodified plan → ExitPlanMode allowed (historical plan)."""
        subprocess.run(["git", "add", ".claude/plans/impl-plan.md"], cwd=plan_review_repo, check=True)
        subprocess.run(["git", "commit", "-m", "plan"], cwd=plan_review_repo, check=True)
        result = run_hook(
            REQUIRE_PLAN_REVIEW_HOOK,
            {**exitplanmode_input(), "session_id": "test-session-epm-committed"},
            cwd=plan_review_repo,
        )
        assert result == "allow"

    def test_no_session_id_denies_exitplanmode(self, plan_review_repo, plan_review_home):
        """No session_id → deny (fail-closed; can't key per-session marker)."""
        result = run_hook(
            REQUIRE_PLAN_REVIEW_HOOK,
            exitplanmode_input(),  # no session_id key
            cwd=plan_review_repo,
        )
        assert result == "deny"

    def test_no_session_id_exitplanmode_deny_reason(self, plan_review_repo, plan_review_home):
        """No session_id deny reason uses ExitPlanMode-specific wording, not a generic message."""
        reason = run_hook_reason(
            REQUIRE_PLAN_REVIEW_HOOK,
            exitplanmode_input(),  # no session_id key
            cwd=plan_review_repo,
        )
        assert reason is not None and "plan presentation" in reason.lower()

    def test_no_file_path_exitplanmode_denies_without_crash(self, plan_review_repo, plan_review_home):
        """ExitPlanMode has no file_path — must deny cleanly, no crash (set -u safe).

        TARGET_PATH resolves to empty string for ExitPlanMode payloads (no
        .tool_input.file_path field). The `if [ -n "$TARGET_PATH" ]` guard
        skips the scope filter, and the hook falls through to emit_deny.
        This test confirms the hook does not crash under set -uo pipefail.
        """
        payload = exitplanmode_input()
        # Confirm no file_path field — the helper must not include it.
        assert "file_path" not in payload.get("tool_input", {})
        result = run_hook(
            REQUIRE_PLAN_REVIEW_HOOK,
            {**payload, "session_id": "test-session-epm-nopath"},
            cwd=plan_review_repo,
        )
        assert result == "deny"

    def test_active_marker_does_not_bypass_exitplanmode(self, plan_review_repo, plan_review_home):
        """Active marker (plan-review in progress) → ExitPlanMode still denied.

        Contrasts with Write/Edit, which are allowed through during an active
        review so the skill's own file writes don't self-deny. ExitPlanMode
        is never called by the plan-review skill itself, so an active marker
        indicates review is incomplete — ExitPlanMode must remain blocked.
        """
        sid = "test-session-epm-active"
        active_dir = plan_review_home / ".claude" / ".plan-review-active.d"
        active_dir.mkdir(parents=True, exist_ok=True)
        (active_dir / sid).write_text(str(os.getpid()))  # live PID
        result = run_hook(
            REQUIRE_PLAN_REVIEW_HOOK,
            {**exitplanmode_input(), "session_id": sid},
            cwd=plan_review_repo,
        )
        assert result == "deny"

    def test_other_sessions_completion_marker_does_not_authorize_exitplanmode(
        self, plan_review_repo, plan_review_home
    ):
        """Session A's completion marker does not release session B's ExitPlanMode gate.

        The hook keys completion markers per-session ($REPO_HASH.$SESSION_ID).
        A marker written by a different session must not authorize ExitPlanMode,
        mirroring the cross-session isolation that exists for Write/Edit.
        """
        other_sid = "test-session-epm-other"
        write_plan_review_marker(plan_review_home, plan_review_repo, other_sid)
        result = run_hook(
            REQUIRE_PLAN_REVIEW_HOOK,
            {**exitplanmode_input(), "session_id": "test-session-epm-current"},
            cwd=plan_review_repo,
        )
        assert result == "deny"


def test_settings_exitplanmode_matcher_exists_and_isolated():
    """settings.json has ExitPlanMode in its own matcher block, not in Edit|Write|MultiEdit.

    ExitPlanMode must be isolated from the Edit|Write|MultiEdit block because
    that block also runs ask-review-permissions.sh and
    require-worktree-for-file-writes.sh, which must not fire on ExitPlanMode.
    """
    settings_path = CLAUDE_DIR / "settings.json"
    settings = json.loads(settings_path.read_text())
    pre_tool_use = settings.get("hooks", {}).get("PreToolUse", [])

    # ExitPlanMode must have exactly one dedicated matcher block.
    exitplanmode_blocks = [b for b in pre_tool_use if b.get("matcher") == "ExitPlanMode"]
    assert len(exitplanmode_blocks) == 1, (
        "ExitPlanMode should have exactly one matcher block in PreToolUse"
    )
    hook_commands = [h["command"] for h in exitplanmode_blocks[0].get("hooks", [])]
    assert any(cmd.endswith("require-plan-review.sh") for cmd in hook_commands), (
        "ExitPlanMode block must include require-plan-review.sh"
    )
    assert not any("ask-review-permissions" in cmd for cmd in hook_commands), (
        "ExitPlanMode block must not include ask-review-permissions.sh"
    )
    assert not any("require-worktree" in cmd for cmd in hook_commands), (
        "ExitPlanMode block must not include require-worktree-for-file-writes.sh"
    )

    # ExitPlanMode must NOT appear inside the Edit|Write|MultiEdit block.
    write_edit_blocks = [
        b for b in pre_tool_use
        if "Write" in b.get("matcher", "") and "Edit" in b.get("matcher", "")
    ]
    for block in write_edit_blocks:
        assert "ExitPlanMode" not in block.get("matcher", ""), (
            "ExitPlanMode must not be bundled into the Edit|Write|MultiEdit matcher"
        )
