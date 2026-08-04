"""Tests for cleanup-worktree-anchor-nudge-marker.sh.

SessionEnd destructor for nudge-worktree-anchor.sh's per-session state file
(~/.claude/.worktree-anchor-nudge.d/<session_id>) — without it, that
directory grows one file per session forever, mirroring the gap
cleanup-handoff-nudge-marker.sh closes for nudge-handoff-near-context-cap.sh.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from helpers import (
    CANARY_CONTENT,
    HOOKS_DIR,
    TRAVERSAL_SESSION_ID,
    plant_traversal_canary,
)

CLEANUP_HOOK = HOOKS_DIR / "cleanup-worktree-anchor-nudge-marker.sh"


def _state_dir(home: Path) -> Path:
    return home / ".claude" / ".worktree-anchor-nudge.d"


def _run(
    payload: dict | None, home: Path, extra_env: dict | None = None
) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(home)}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(CLEANUP_HOOK)],
        input=json.dumps(payload) if payload is not None else "",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


class TestCleanupWorktreeAnchorNudgeMarker:
    def test_deletes_the_state_file_for_this_session(self, isolated_home):
        state_dir = _state_dir(isolated_home)
        state_dir.mkdir(parents=True)
        (state_dir / "session-a").write_text("/some/repo\n")

        result = _run({"session_id": "session-a"}, isolated_home)

        assert result.returncode == 0
        assert not (state_dir / "session-a").exists()

    def test_leaves_other_sessions_state_files_untouched(self, isolated_home):
        state_dir = _state_dir(isolated_home)
        state_dir.mkdir(parents=True)
        (state_dir / "session-a").write_text("/repo-a\n")
        (state_dir / "session-b").write_text("/repo-b\n")

        result = _run({"session_id": "session-a"}, isolated_home)

        assert result.returncode == 0
        assert not (state_dir / "session-a").exists()
        assert (state_dir / "session-b").exists()

    def test_no_state_file_present_exits_zero(self, isolated_home):
        result = _run({"session_id": "no-such-session"}, isolated_home)
        assert result.returncode == 0

    def test_missing_session_id_exits_zero(self, isolated_home):
        result = _run({"other": "field"}, isolated_home)
        assert result.returncode == 0

    def test_empty_stdin_exits_zero(self, isolated_home):
        result = _run(None, isolated_home)
        assert result.returncode == 0

    def test_malformed_json_exits_zero(self, isolated_home):
        result = subprocess.run(
            [str(CLEANUP_HOOK)],
            input="not valid json {{",
            env={**os.environ, "HOME": str(isolated_home)},
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0

    def test_deletes_the_state_file_at_config_dir_when_set(self, isolated_home, tmp_path):
        """CLAUDE_CONFIG_DIR relocates the state directory: the per-session
        state file is deleted from CONFIG_DIR, not from $HOME/.claude."""
        config_dir = tmp_path / "profile"
        state_dir = config_dir / ".worktree-anchor-nudge.d"
        state_dir.mkdir(parents=True)
        (state_dir / "session-a").write_text("/some/repo\n")

        result = _run(
            {"session_id": "session-a"},
            isolated_home,
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        )

        assert result.returncode == 0
        assert not (state_dir / "session-a").exists()

    def test_traversal_session_id_does_not_delete_files_outside_state_dir(
        self, isolated_home
    ):
        """A session_id containing '../' must not let the rm -f escape the
        state directory (Fix 1's guard, applied identically to this
        destructor)."""
        canary = plant_traversal_canary(isolated_home)

        result = _run({"session_id": TRAVERSAL_SESSION_ID}, isolated_home)

        assert result.returncode == 0
        assert canary.read_text() == CANARY_CONTENT
