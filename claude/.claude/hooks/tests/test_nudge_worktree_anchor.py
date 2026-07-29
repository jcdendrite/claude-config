"""Tests for nudge-worktree-anchor.sh.

The hook reports one condition: this session is working from the MAIN working
tree of a repo that requires worktrees, while a linked worktree exists on
disk. Everything else must be silent, and every path must exit 0 — a non-zero
exit from a UserPromptSubmit hook risks disrupting prompt submission.
"""
from __future__ import annotations

import json
import os
import subprocess

from helpers import (
    CANARY_CONTENT,
    HOOKS_DIR,
    TRAVERSAL_SESSION_ID,
    plant_traversal_canary,
)

NUDGE_HOOK = HOOKS_DIR / "nudge-worktree-anchor.sh"

SESSION_ID = "anchor-test-session"


def _run(cwd, home, session_id: str | None = SESSION_ID) -> subprocess.CompletedProcess:
    payload: dict = {"cwd": str(cwd)}
    if session_id is not None:
        payload["session_id"] = session_id
    return subprocess.run(
        ["bash", str(NUDGE_HOOK)],
        input=json.dumps(payload),
        cwd=str(cwd),
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True,
    )


def _context(result: subprocess.CompletedProcess) -> str | None:
    """The advisory string, or None when the hook stayed silent."""
    if not result.stdout.strip():
        return None
    payload = json.loads(result.stdout)
    return payload["hookSpecificOutput"]["additionalContext"]


class TestEmitsOnlyInTheDriftedState:
    def test_emits_from_main_tree_when_linked_worktree_exists(
        self, isolated_home, opted_in_with_worktree
    ):
        repo, wt = opted_in_with_worktree
        result = _run(repo, isolated_home)

        assert result.returncode == 0
        context = _context(result)
        assert context is not None, "expected an advisory from the main tree"
        assert str(repo) in context
        assert str(wt) in context, "advisory should name the worktree that exists"

    def test_advisory_declares_the_userpromptsubmit_event(
        self, isolated_home, opted_in_with_worktree
    ):
        """The harness routes additionalContext by hookEventName; a mismatched
        name silently drops the advisory."""
        repo, _wt = opted_in_with_worktree
        result = _run(repo, isolated_home)

        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"

    def test_advisory_names_the_recovery_action(self, isolated_home, opted_in_with_worktree):
        repo, _wt = opted_in_with_worktree
        context = _context(_run(repo, isolated_home))
        assert "EnterWorktree" in context

    def test_silent_from_inside_the_linked_worktree(
        self, isolated_home, opted_in_with_worktree
    ):
        _repo, wt = opted_in_with_worktree
        result = _run(wt, isolated_home)

        assert result.returncode == 0
        assert _context(result) is None, "an anchored session must not be nudged"

    def test_silent_when_enforcement_inactive(self, isolated_home, non_opted_repo, tmp_path):
        """A repo that never opted in sees no change from this hook, even with
        a linked worktree present."""
        wt = tmp_path / "non-opted-tree"
        subprocess.run(
            ["git", "worktree", "add", "-b", "feature", str(wt)],
            cwd=non_opted_repo,
            check=True,
        )
        result = _run(non_opted_repo, isolated_home)

        assert result.returncode == 0
        assert _context(result) is None

    def test_silent_when_no_linked_worktree_exists(self, isolated_home, opted_in_repo):
        """Opted in but no worktree yet — the normal state just before
        `git worktree add`. Nothing has gone wrong."""
        result = _run(opted_in_repo, isolated_home)

        assert result.returncode == 0
        assert _context(result) is None

    def test_silent_when_the_recorded_worktree_no_longer_exists_on_disk(
        self, isolated_home, opted_in_with_worktree
    ):
        """`git worktree list` still reports entries whose directory was
        deleted but not pruned. A stale entry is not a reason to nudge."""
        repo, wt = opted_in_with_worktree
        subprocess.run(["rm", "-rf", str(wt)], check=True)

        result = _run(repo, isolated_home)

        assert result.returncode == 0
        assert _context(result) is None, (
            "a pruned-but-unregistered worktree path must not count as existing"
        )


class TestExitsZeroOnEveryPath:
    """A non-zero exit from UserPromptSubmit risks disrupting prompt
    submission, so every degenerate input must still exit 0 and stay quiet."""

    def test_non_repo_cwd(self, isolated_home, tmp_path):
        outside = tmp_path / "not-a-repo"
        outside.mkdir()
        result = _run(outside, isolated_home)
        assert result.returncode == 0
        assert _context(result) is None

    def test_missing_session_id(self, isolated_home, opted_in_with_worktree):
        """Without a session id there is nowhere to record 'already reported',
        and an advisory that cannot dedup would repeat on every prompt."""
        repo, _wt = opted_in_with_worktree
        result = _run(repo, isolated_home, session_id=None)
        assert result.returncode == 0
        assert _context(result) is None

    def test_empty_stdin(self, isolated_home, opted_in_with_worktree):
        repo, _wt = opted_in_with_worktree
        result = subprocess.run(
            ["bash", str(NUDGE_HOOK)],
            input="",
            cwd=str(repo),
            env={**os.environ, "HOME": str(isolated_home)},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_malformed_json_stdin(self, isolated_home, opted_in_with_worktree):
        repo, _wt = opted_in_with_worktree
        result = subprocess.run(
            ["bash", str(NUDGE_HOOK)],
            input="{not json",
            cwd=str(repo),
            env={**os.environ, "HOME": str(isolated_home)},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_bare_repo(self, isolated_home, tmp_path):
        bare = tmp_path / "bare.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        result = _run(bare, isolated_home)
        assert result.returncode == 0
        assert _context(result) is None


class TestSessionIdPathTraversal:
    """A session_id containing '../' must not let STATE_FILE (fed straight
    from the hook payload's `.session_id`) escape the state directory —
    neither on the write path (fire) nor on the delete path (_rearm's
    `rm -f`)."""

    def test_traversal_session_id_does_not_touch_files_outside_state_dir(
        self, isolated_home, opted_in_with_worktree
    ):
        repo, _wt = opted_in_with_worktree
        state_dir = isolated_home / ".claude" / ".worktree-anchor-nudge.d"
        canary = plant_traversal_canary(isolated_home)

        result = _run(repo, isolated_home, session_id=TRAVERSAL_SESSION_ID)

        assert result.returncode == 0
        assert canary.read_text() == CANARY_CONTENT, (
            "a traversal session_id must not overwrite a file outside the state dir"
        )
        assert not state_dir.exists() or list(state_dir.iterdir()) == [], (
            "the invalid id must not create an entry in the state dir either"
        )

    def test_traversal_session_id_does_not_delete_files_outside_state_dir(
        self, isolated_home, opted_in_with_worktree
    ):
        """Mirrors the write case for _rearm's `rm -f "$STATE_FILE"`, exercised
        by a request that reaches a re-arming branch (anchored worktree)."""
        repo, wt = opted_in_with_worktree
        canary = plant_traversal_canary(isolated_home)

        result = _run(wt, isolated_home, session_id=TRAVERSAL_SESSION_ID)

        assert result.returncode == 0
        assert canary.exists() and canary.read_text() == CANARY_CONTENT, (
            "a traversal session_id must not delete a file outside the state dir"
        )


class TestReArmsOnStateChange:
    """Anchor state is not monotonic — a session can enter a worktree and
    later drift back out. The one-shot per-session dedup used by the other
    nudge hooks would suppress a genuine second occurrence."""

    def test_second_consecutive_prompt_is_deduped(
        self, isolated_home, opted_in_with_worktree
    ):
        repo, _wt = opted_in_with_worktree

        assert _context(_run(repo, isolated_home)) is not None
        assert _context(_run(repo, isolated_home)) is None, (
            "the same unchanged state must not be reported every prompt"
        )

    def test_re_fires_after_anchored_then_unanchored(
        self, isolated_home, opted_in_with_worktree
    ):
        repo, wt = opted_in_with_worktree

        assert _context(_run(repo, isolated_home)) is not None  # drifted: reported
        assert _context(_run(wt, isolated_home)) is None        # anchored: silent, re-arms
        assert _context(_run(repo, isolated_home)) is not None, (
            "drifting back out after anchoring is a genuine second occurrence"
        )

    def test_enforcement_inactive_also_re_arms(
        self, isolated_home, opted_in_with_worktree, non_opted_repo
    ):
        """Re-arming must not depend on passing through the worktree branch
        specifically — any not-met evaluation clears the recorded report."""
        repo, _wt = opted_in_with_worktree

        assert _context(_run(repo, isolated_home)) is not None
        assert _context(_run(non_opted_repo, isolated_home)) is None
        assert _context(_run(repo, isolated_home)) is not None

    def test_no_linked_worktree_branch_also_re_arms(
        self, isolated_home, opted_in_with_worktree
    ):
        """The third false-branch (no linked worktree exists at all, the
        normal state just before `git worktree add`) must re-arm just like
        the other two: fire, then the worktree is removed (silent, re-arms),
        then a fresh worktree reappears and the drifted state fires again."""
        repo, wt = opted_in_with_worktree

        assert _context(_run(repo, isolated_home)) is not None  # drifted: reported

        subprocess.run(
            ["git", "worktree", "remove", "--force", str(wt)], cwd=repo, check=True
        )
        assert _context(_run(repo, isolated_home)) is None, (
            "no worktree at all must be silent"
        )

        new_wt = wt.parent / "recreated-tree"
        subprocess.run(
            ["git", "worktree", "add", "-b", "feature-2", str(new_wt)], cwd=repo, check=True
        )
        assert _context(_run(repo, isolated_home)) is not None, (
            "a worktree reappearing after having none is a genuine second occurrence"
        )
