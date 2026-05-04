"""Tests for require-ready-for-review.sh."""
from __future__ import annotations

import hashlib
import os
import subprocess
import time
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    SKILLS_DIR,
    bash_input,
    edit_input,
    extract_skill_command,
    git_toplevel,
    head_sha,
    run_hook,
    run_skill_command,
)

READY_FOR_REVIEW_HOOK = HOOKS_DIR / "require-ready-for-review.sh"
READY_FOR_REVIEW_SKILL = SKILLS_DIR / "ready-for-review" / "SKILL.md"


@pytest.fixture
def fake_gh_pr_exists(tmp_path, monkeypatch):
    """Inject a fake gh that reports an open PR (`pr view ...` returns 42)."""
    bin_dir = tmp_path / "fake-bin-pr"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(
        '#!/bin/bash\n'
        'case "$*" in *"pr view"*) echo 42 ;; *) exit 1 ;; esac\n'
    )
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return gh


@pytest.fixture
def fake_gh_no_pr(tmp_path, monkeypatch):
    """Inject a fake gh that reports no open PR (any subcommand exits 1)."""
    bin_dir = tmp_path / "fake-bin-nopr"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text("#!/bin/bash\nexit 1\n")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return gh


@pytest.fixture
def repo_on_feature_branch(tmp_path):
    """Repo with `main` configured as default and a `feature` branch checked
    out. Fakes the origin/main ref so the hook's default-branch detection
    works without a real remote."""
    repo = tmp_path / "rfr-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "f").write_text("a\n")
    subprocess.run(["git", "add", "f"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "checkout", "-q", "-b", "feature"], cwd=repo, check=True)
    return repo


def rfr_active_marker(home: Path, session_id: str) -> Path:
    return home / ".claude" / ".ready-for-review-active.d" / session_id


def rfr_completion_marker(home: Path, repo: Path, session_id: str) -> Path:
    repo_hash = hashlib.sha256(git_toplevel(repo).encode()).hexdigest()
    return (
        home / ".claude" / "ready-for-review-markers" / f"{repo_hash}.{session_id}"
    )


class TestRequireReadyForReview:
    """Push gate: requires /ready-for-review to have run before pushing to a
    branch with an open PR (or marking a draft PR ready). Two-marker layout:
    active (presence-only, bypasses during skill execution) and completion
    (HEAD SHA, allows pushes against the recorded HEAD)."""

    # -- Bypass shapes (no marker required) ------------------------------

    @pytest.mark.parametrize(
        "command",
        [
            "git status",
            "git log --oneline",
            "git commit -m foo",
            "echo git push",
        ],
    )
    def test_non_push_commands_allowed(
        self, isolated_home, repo_on_feature_branch, command
    ):
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input(command, session_id="s"),
                cwd=repo_on_feature_branch,
            )
            == "allow"
        )

    def test_non_bash_tool_allowed(self, isolated_home):
        assert (
            run_hook(READY_FOR_REVIEW_HOOK, edit_input("/tmp/foo.txt")) == "allow"
        )

    @pytest.mark.parametrize(
        "command",
        [
            "git push --dry-run",
            "git push origin feature --dry-run",
            "git push origin --delete feature",
            "git push origin -d feature",
            "git push origin :feature",
        ],
    )
    def test_destructive_or_dry_push_shapes_allowed(
        self,
        isolated_home,
        repo_on_feature_branch,
        fake_gh_pr_exists,
        command,
    ):
        """Even on a PR branch, --dry-run / --delete / colon-deletion don't
        publish a reviewable artifact change. Bypass without checking marker."""
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input(command, session_id="s"),
                cwd=repo_on_feature_branch,
            )
            == "allow"
        )

    def test_tags_only_push_allowed(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin --tags", session_id="s"),
                cwd=repo_on_feature_branch,
            )
            == "allow"
        )

    def test_tags_with_branch_still_gated(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """`git push --tags origin feature` pushes BOTH tags AND a branch ref —
        the branch is reviewable, so gate."""
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push --tags origin feature", session_id="s"),
                cwd=repo_on_feature_branch,
            )
            == "deny"
        )

    def test_default_branch_push_allowed(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """Push from default branch has no PR review semantics — bypass."""
        subprocess.run(
            ["git", "checkout", "-q", "main"],
            cwd=repo_on_feature_branch,
            check=True,
        )
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push", session_id="s"),
                cwd=repo_on_feature_branch,
            )
            == "allow"
        )

    def test_branch_with_no_pr_allowed(
        self, isolated_home, repo_on_feature_branch, fake_gh_no_pr
    ):
        """Branch hasn't had a PR opened yet — nothing to gate."""
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id="s"),
                cwd=repo_on_feature_branch,
            )
            == "allow"
        )

    def test_outside_git_repo_allowed(self, isolated_home, tmp_path):
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push", session_id="s"),
                cwd=non_repo,
            )
            == "allow"
        )

    # -- Active-marker bypass --------------------------------------------

    def test_fresh_active_marker_allows(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """During /ready-for-review's own iteration (step 3 fix → push →
        loop), the active marker bypasses the gate so the skill doesn't
        self-deny."""
        sid = "session-active"
        marker_dir = isolated_home / ".claude" / ".ready-for-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).touch()
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id=sid),
                cwd=repo_on_feature_branch,
            )
            == "allow"
        )

    def test_stale_active_marker_falls_through(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """>60min old marker doesn't bypass; with no completion marker, deny."""
        sid = "session-stale"
        marker_dir = isolated_home / ".claude" / ".ready-for-review-active.d"
        marker_dir.mkdir(parents=True)
        marker = marker_dir / sid
        marker.touch()
        ninety_min_ago = time.time() - 90 * 60
        os.utime(marker, (ninety_min_ago, ninety_min_ago))
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id=sid),
                cwd=repo_on_feature_branch,
            )
            == "deny"
        )

    def test_other_sessions_active_marker_does_not_bypass(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """Per-session keying: A's active marker must NOT authorize B's push."""
        marker_dir = isolated_home / ".claude" / ".ready-for-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / "session-A").touch()
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id="session-B"),
                cwd=repo_on_feature_branch,
            )
            == "deny"
        )

    def test_active_marker_mtime_refreshed_on_bypass(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """Long-running skill mitigation: hook touches marker on each bypass
        so a session approaching the 60-min cutoff doesn't get blocked."""
        sid = "session-long"
        marker_dir = isolated_home / ".claude" / ".ready-for-review-active.d"
        marker_dir.mkdir(parents=True)
        marker = marker_dir / sid
        marker.touch()
        fifty_min_ago = time.time() - 50 * 60
        os.utime(marker, (fifty_min_ago, fifty_min_ago))
        pre_mtime = marker.stat().st_mtime
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id=sid),
                cwd=repo_on_feature_branch,
            )
            == "allow"
        )
        assert marker.stat().st_mtime > pre_mtime, (
            "active marker mtime must be refreshed on bypass"
        )

    # -- Completion-marker check ------------------------------------------

    def test_no_marker_denies(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id="s"),
                cwd=repo_on_feature_branch,
            )
            == "deny"
        )

    def test_completion_marker_matching_head_allows(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        sid = "s"
        marker = rfr_completion_marker(isolated_home, repo_on_feature_branch, sid)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(head_sha(repo_on_feature_branch) + "\n")
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id=sid),
                cwd=repo_on_feature_branch,
            )
            == "allow"
        )

    def test_completion_marker_stale_head_denies(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """New commit after gate ran → HEAD moves → marker stale → deny."""
        sid = "s"
        marker = rfr_completion_marker(isolated_home, repo_on_feature_branch, sid)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(head_sha(repo_on_feature_branch) + "\n")
        (repo_on_feature_branch / "f").write_text("b\n")
        subprocess.run(
            ["git", "add", "f"], cwd=repo_on_feature_branch, check=True
        )
        subprocess.run(
            ["git", "commit", "-qm", "more"],
            cwd=repo_on_feature_branch,
            check=True,
        )
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id=sid),
                cwd=repo_on_feature_branch,
            )
            == "deny"
        )

    def test_other_sessions_completion_marker_does_not_authorize(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """Per-session keying: A's completion marker must NOT authorize B's
        push, even with the same HEAD SHA."""
        head = head_sha(repo_on_feature_branch)
        marker_a = rfr_completion_marker(isolated_home, repo_on_feature_branch, "A")
        marker_a.parent.mkdir(parents=True, exist_ok=True)
        marker_a.write_text(head + "\n")
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id="B"),
                cwd=repo_on_feature_branch,
            )
            == "deny"
        )

    def test_no_session_id_denies_when_pr_exists(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """Without session_id in the hook payload, no per-session marker can
        be keyed — deny when a PR exists."""
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature"),
                cwd=repo_on_feature_branch,
            )
            == "deny"
        )

    # -- gh pr ready ------------------------------------------------------

    def test_gh_pr_ready_no_marker_denies(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """Marking a draft PR ready is a handoff signal — gate it the same
        way as push."""
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("gh pr ready", session_id="s"),
                cwd=repo_on_feature_branch,
            )
            == "deny"
        )

    def test_gh_pr_ready_with_completion_marker_allowed(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        sid = "s"
        marker = rfr_completion_marker(isolated_home, repo_on_feature_branch, sid)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(head_sha(repo_on_feature_branch) + "\n")
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("gh pr ready", session_id=sid),
                cwd=repo_on_feature_branch,
            )
            == "allow"
        )

    # -- gh failure → fail-open ------------------------------------------

    def test_gh_failure_fails_open(
        self, isolated_home, repo_on_feature_branch, fake_gh_no_pr
    ):
        """If gh pr view errors (network glitch, gh not configured), the gate
        does not fire — keeps the user unblocked. The skill's prose triggers
        still cover this case."""
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id="s"),
                cwd=repo_on_feature_branch,
            )
            == "allow"
        )

    # -- Skill ↔ hook alignment ------------------------------------------

    def test_skill_activate_command_creates_active_marker(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """SKILL.md activate-gate fixture must produce a marker the hook
        recognizes as a fresh active-session bypass."""
        sid = "test-session-rfr-activate"
        sessions_dir = isolated_home / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / str(os.getpid())).write_text(sid)

        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id=sid),
                cwd=repo_on_feature_branch,
            )
            == "deny"
        ), "precondition: gated push must deny before activation"

        cmd = extract_skill_command(READY_FOR_REVIEW_SKILL, "activate-gate")
        run_skill_command(cmd, cwd=repo_on_feature_branch, isolated_home=isolated_home)

        marker = rfr_active_marker(isolated_home, sid)
        assert marker.exists(), (
            "SKILL.md activate-gate recipe ran but no marker landed at the "
            "path the hook checks — skill and hook disagree on layout."
        )
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id=sid),
                cwd=repo_on_feature_branch,
            )
            == "allow"
        )

    def test_skill_record_completion_command_creates_marker(
        self, isolated_home, repo_on_feature_branch, fake_gh_pr_exists
    ):
        """SKILL.md record-completion fixture must produce a marker keyed by
        repo-hash + session-id with HEAD SHA as content, recognized by the
        hook for HEAD-matching pushes."""
        sid = "test-session-rfr-complete"
        sessions_dir = isolated_home / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / str(os.getpid())).write_text(sid)

        cmd = extract_skill_command(READY_FOR_REVIEW_SKILL, "record-completion")
        run_skill_command(cmd, cwd=repo_on_feature_branch, isolated_home=isolated_home)

        marker = rfr_completion_marker(isolated_home, repo_on_feature_branch, sid)
        assert marker.exists(), (
            "SKILL.md record-completion recipe ran but no marker landed at "
            "the path the hook checks — skill and hook disagree on layout."
        )
        assert marker.read_text().strip() == head_sha(repo_on_feature_branch), (
            "completion marker must hold the local HEAD SHA"
        )
        assert (
            run_hook(
                READY_FOR_REVIEW_HOOK,
                bash_input("git push origin feature", session_id=sid),
                cwd=repo_on_feature_branch,
            )
            == "allow"
        )

    def test_skill_deactivate_command_removes_active_marker(
        self, isolated_home, repo_on_feature_branch
    ):
        """SKILL.md deactivate-gate fixture must remove this session's active
        marker so a subsequent push (without completion marker) is re-gated."""
        sid = "test-session-rfr-deactivate"
        sessions_dir = isolated_home / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / str(os.getpid())).write_text(sid)

        activate_cmd = extract_skill_command(READY_FOR_REVIEW_SKILL, "activate-gate")
        run_skill_command(activate_cmd, cwd=repo_on_feature_branch, isolated_home=isolated_home)
        marker = rfr_active_marker(isolated_home, sid)
        assert marker.exists(), "activate-gate setup did not create the marker"

        deactivate_cmd = extract_skill_command(
            READY_FOR_REVIEW_SKILL, "deactivate-gate"
        )
        run_skill_command(deactivate_cmd, cwd=repo_on_feature_branch, isolated_home=isolated_home)
        assert not marker.exists(), (
            "SKILL.md deactivate-gate recipe ran but the marker is still "
            "present — skill and hook disagree on the marker path."
        )
