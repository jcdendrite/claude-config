"""Tests for require-respond-pr.sh."""
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
    run_hook,
    run_skill_command,
)

RESPOND_PR_HOOK = HOOKS_DIR / "require-respond-pr.sh"
RESPOND_PR_SKILL = SKILLS_DIR / "respond-pr" / "SKILL.md"


@pytest.fixture
def current_repo_foo_bar(tmp_path):
    """Git repo whose origin is https://github.com/foo/bar.git.

    Most respond-pr tests target `foo/bar` in the command URL. The
    cross-repo bypass compares COMMAND_REPO against the current git origin,
    so we need the current repo to also be `foo/bar` for the gate to fire
    as expected on same-repo commands.
    """
    repo = tmp_path / "foo-bar-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/foo/bar.git"],
        cwd=repo,
        check=True,
    )
    return repo


class TestRequireRespondPr:
    @pytest.mark.parametrize(
        "command",
        [
            "gh api repos/foo/bar/pulls/5/comments",
            "gh api repos/foo/bar/pulls/5/reviews",
            "gh api repos/foo/bar/issues/5/comments",
            "gh pr comment 5 --body test",
            "gh pr review 5 --approve",
            "gh api repos/foo/bar/pulls/5/comments -F body=hi",
            "gh api repos/foo/bar/pulls/comments/12345 -X PATCH -F body=oops",
            "gh api repos/foo/bar/issues/comments/12345 -X PATCH -F body=oops",
            "gh api repos/foo/bar/pulls/comments/12345",
            "gh api repos/foo/bar/pulls/comments/12345 -X DELETE",
        ],
    )
    def test_matching_commands_denied(self, isolated_home, current_repo_foo_bar, command):
        assert run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=current_repo_foo_bar) == "deny"

    @pytest.mark.parametrize(
        "command",
        [
            "gh pr view 5",
            "gh pr list",
            "gh api user",
            "gh pr checkout 5",
            "echo foo",
            "git status",
            "gh api repos/foo/bar/pulls/5",
            "gh api repos/foo/bar/pulls/comments",
            "gh api repos/foo/bar/contents/comments/12345",
        ],
    )
    def test_non_matching_commands_allowed(self, isolated_home, current_repo_foo_bar, command):
        assert run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=current_repo_foo_bar) == "allow"

    # -- Bypass-marker behavior --------------------------------------------
    # Marker layout: ~/.claude/.respond-pr-active.d/<session_id>. The skill
    # writes one file per session at entry; the hook checks the file matching
    # THIS request's session_id (from JSON payload). Per-session keying
    # prevents two failure modes the prior singleton path had:
    #   (1) Cleanup thrash — session A's `rm -f` deleting B's marker.
    #   (2) Bypass leak — session A's marker silently authorizing B's
    #       gh calls even though B never ran /respond-pr.

    @pytest.mark.parametrize(
        "command",
        [
            "gh api repos/foo/bar/pulls/5/comments",
            "gh pr comment 5 --body test",
            "gh api repos/foo/bar/pulls/comments/12345 -X PATCH -F body=oops",
            "gh api repos/foo/bar/issues/comments/12345 -X PATCH -F body=oops",
        ],
    )
    def test_fresh_bypass_marker_allows(self, isolated_home, current_repo_foo_bar, command):
        sid = "test-session-fresh"
        marker_dir = isolated_home / ".claude" / ".respond-pr-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).write_text(str(os.getpid()))
        assert (
            run_hook(RESPOND_PR_HOOK, bash_input(command, session_id=sid), cwd=current_repo_foo_bar)
            == "allow"
        )

    def test_dead_pid_bypass_marker_evicts_and_denies(self, isolated_home, current_repo_foo_bar):
        """Orphaned marker with a dead PID is evicted and the gate denies."""
        sid = "test-session-dead-pid"
        marker_dir = isolated_home / ".claude" / ".respond-pr-active.d"
        marker_dir.mkdir(parents=True)
        marker = marker_dir / sid
        marker.write_text("99999999")  # PID outside Linux/macOS max range → always dead
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input("gh api repos/foo/bar/pulls/5/comments", session_id=sid),
                cwd=current_repo_foo_bar,
            )
            == "deny"
        )
        assert not marker.exists(), "hook must evict the orphan marker on dead PID"

    def test_other_sessions_marker_does_not_leak_bypass(self, isolated_home, current_repo_foo_bar):
        """Regression: a marker for session A must NOT bypass session B's
        gated calls. This is the silent failure mode of the prior singleton
        design — while any session held the marker, every other session's
        matching gh calls bypassed the gate."""
        marker_dir = isolated_home / ".claude" / ".respond-pr-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / "session-A").touch()
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input("gh api repos/foo/bar/pulls/5/comments", session_id="session-B"),
                cwd=current_repo_foo_bar,
            )
            == "deny"
        )

    def test_no_session_id_in_input_denies(self, isolated_home, current_repo_foo_bar):
        """Without session_id in the hook payload, bypass is impossible —
        deny even if a marker file exists in the dir."""
        marker_dir = isolated_home / ".claude" / ".respond-pr-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / "any-session").touch()
        # bash_input() with session_id=None omits the field entirely.
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input("gh api repos/foo/bar/pulls/5/comments"),
                cwd=current_repo_foo_bar,
            )
            == "deny"
        )

    def test_marker_dir_missing_denies(self, isolated_home, current_repo_foo_bar):
        """Sessions dir entirely absent (e.g., capture-session-id.sh never
        ran) → deny. The skill is responsible for failing loudly in that
        case; the hook just denies safely."""
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input("gh api repos/foo/bar/pulls/5/comments", session_id="some-session"),
                cwd=current_repo_foo_bar,
            )
            == "deny"
        )

    def test_alive_pid_bypass_marker_allows(self, isolated_home, current_repo_foo_bar):
        """Active marker with a live PID bypasses the gate for any session duration."""
        sid = "long-run-session"
        marker_dir = isolated_home / ".claude" / ".respond-pr-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).write_text(str(os.getpid()))
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input("gh api repos/foo/bar/pulls/5/comments", session_id=sid),
                cwd=current_repo_foo_bar,
            )
            == "allow"
        )

    def test_non_bash_tool_allowed(self, isolated_home):
        assert run_hook(RESPOND_PR_HOOK, edit_input("/tmp/foo.txt")) == "allow"

    # -- Cross-repo bypass --------------------------------------------------
    # Regression: the gate originally fired on any `(pulls|issues)/N/...`
    # URL regardless of repo, which false-positived on cross-repo research
    # reads like `gh api repos/anthropics/claude-code/issues/12962/comments`
    # from inside an unrelated project.

    @pytest.mark.parametrize(
        "command",
        [
            "gh api repos/other/repo/pulls/5/comments",
            "gh api repos/other/repo/pulls/5/reviews",
            "gh api repos/other/repo/issues/5/comments",
            "gh pr comment 5 -R other/repo --body test",
            "gh pr review 5 --repo other/repo --approve",
            "gh pr comment 5 --repo=other/repo --body test",
            "gh api repos/other/repo/pulls/comments/12345 -X PATCH -F body=hi",
            "gh api repos/other/repo/issues/comments/12345 -X PATCH -F body=hi",
        ],
    )
    def test_cross_repo_commands_allowed(self, isolated_home, current_repo_foo_bar, command):
        assert run_hook(RESPOND_PR_HOOK, bash_input(command), cwd=current_repo_foo_bar) == "allow"

    def test_ssh_origin_cross_repo_allowed(self, isolated_home, tmp_path):
        """SSH-form origin (git@github.com:owner/repo.git) must parse too."""
        repo = tmp_path / "ssh-repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "git@github.com:foo/bar.git"],
            cwd=repo,
            check=True,
        )
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input("gh api repos/other/repo/issues/5/comments"),
                cwd=repo,
            )
            == "allow"
        )

    def test_ssh_origin_same_repo_denied(self, isolated_home, tmp_path):
        repo = tmp_path / "ssh-repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "git@github.com:foo/bar.git"],
            cwd=repo,
            check=True,
        )
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input("gh api repos/foo/bar/issues/5/comments"),
                cwd=repo,
            )
            == "deny"
        )

    # -- Skill ↔ hook alignment -------------------------------------------
    # These execute the enable/disable bypass recipes verbatim from
    # respond-pr SKILL.md. If the skill body drifts from the marker layout
    # require-respond-pr.sh expects, these fail.

    def test_skill_enable_command_creates_bypass_marker(
        self, isolated_home, current_repo_foo_bar
    ):
        """Run the SKILL.md enable-bypass recipe and verify the resulting
        marker authorizes a previously-gated gh command."""
        sid = "test-session-respond-pr-enable"
        sessions_dir = isolated_home / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / str(os.getpid())).write_text(sid)

        gated_command = "gh api repos/foo/bar/pulls/5/comments"
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input(gated_command, session_id=sid),
                cwd=current_repo_foo_bar,
            )
            == "deny"
        ), "precondition: gh comment fetch must be gated before bypass is enabled"

        enable_command = extract_skill_command(RESPOND_PR_SKILL, "enable-bypass")
        run_skill_command(enable_command, cwd=current_repo_foo_bar, isolated_home=isolated_home)

        marker = isolated_home / ".claude" / ".respond-pr-active.d" / sid
        assert marker.exists(), (
            "SKILL.md enable-bypass recipe ran but no marker landed at the "
            "path the hook checks — the skill and hook disagree on layout."
        )
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input(gated_command, session_id=sid),
                cwd=current_repo_foo_bar,
            )
            == "allow"
        )

    def test_skill_disable_command_removes_bypass_marker(
        self, isolated_home, current_repo_foo_bar
    ):
        """Run enable then disable from SKILL.md; verify the disable recipe
        removes the marker and the hook re-gates."""
        sid = "test-session-respond-pr-disable"
        sessions_dir = isolated_home / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / str(os.getpid())).write_text(sid)

        enable_command = extract_skill_command(RESPOND_PR_SKILL, "enable-bypass")
        run_skill_command(enable_command, cwd=current_repo_foo_bar, isolated_home=isolated_home)
        marker = isolated_home / ".claude" / ".respond-pr-active.d" / sid
        assert marker.exists(), "enable-bypass setup did not create the marker"

        disable_command = extract_skill_command(RESPOND_PR_SKILL, "disable-bypass")
        run_skill_command(disable_command, cwd=current_repo_foo_bar, isolated_home=isolated_home)

        assert not marker.exists(), (
            "SKILL.md disable-bypass recipe ran but the marker is still "
            "present — the skill and hook disagree on the marker path."
        )
        assert (
            run_hook(
                RESPOND_PR_HOOK,
                bash_input("gh api repos/foo/bar/pulls/5/comments", session_id=sid),
                cwd=current_repo_foo_bar,
            )
            == "deny"
        )
