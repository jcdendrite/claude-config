"""Tests for cleanup-handoff-nudge-marker.sh.

SessionEnd destructor for nudge-handoff-near-context-cap.sh's per-session
marker files (~/.claude/.handoff-nudge-fired.d/<session_id> and the
<session_id>-drift sidecar) — without it, that directory grows one (or two)
files per session forever.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from helpers import HOOKS_DIR

CLEANUP_HOOK = HOOKS_DIR / "cleanup-handoff-nudge-marker.sh"


def _marker_dir(home: Path) -> Path:
    return home / ".claude" / ".handoff-nudge-fired.d"


def _run(payload: dict | None, home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(CLEANUP_HOOK)],
        input=json.dumps(payload) if payload is not None else "",
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True,
        check=False,
    )


class TestCleanupHandoffNudgeMarker:
    def test_deletes_the_fired_marker_for_this_session(self, isolated_home):
        marker_dir = _marker_dir(isolated_home)
        marker_dir.mkdir(parents=True)
        (marker_dir / "session-a").touch()

        result = _run({"session_id": "session-a"}, isolated_home)

        assert result.returncode == 0
        assert not (marker_dir / "session-a").exists()

    def test_deletes_the_drift_marker_for_this_session(self, isolated_home):
        marker_dir = _marker_dir(isolated_home)
        marker_dir.mkdir(parents=True)
        (marker_dir / "session-a-drift").touch()

        result = _run({"session_id": "session-a"}, isolated_home)

        assert result.returncode == 0
        assert not (marker_dir / "session-a-drift").exists()

    def test_leaves_other_sessions_markers_untouched(self, isolated_home):
        marker_dir = _marker_dir(isolated_home)
        marker_dir.mkdir(parents=True)
        (marker_dir / "session-a").touch()
        (marker_dir / "session-b").touch()
        (marker_dir / "session-b-drift").touch()

        result = _run({"session_id": "session-a"}, isolated_home)

        assert result.returncode == 0
        assert not (marker_dir / "session-a").exists()
        assert (marker_dir / "session-b").exists()
        assert (marker_dir / "session-b-drift").exists()

    def test_no_marker_present_exits_zero(self, isolated_home):
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

    def test_traversal_session_id_does_not_delete_files_outside_marker_dir(
        self, isolated_home
    ):
        """A session_id containing '../' must not let either rm -f target
        (the fired marker or its -drift sidecar) escape .handoff-nudge-fired.d
        (guarded by _lib_valid_session_id_component, same as every other
        hook that builds a filesystem path from session_id).

        The marker directory must already exist for this to be a meaningful
        check: `rm -f` on a path that walks through a nonexistent directory
        component is a harmless no-op regardless of the guard, so a bare
        $HOME with no prior nudge activity would make the traversal inert by
        accident rather than by the guard."""
        marker_dir = _marker_dir(isolated_home)
        marker_dir.mkdir(parents=True)
        canary = isolated_home / ".claude" / "canary"
        canary.write_text("untouched\n")
        drift_canary = isolated_home / ".claude" / "canary-drift"
        drift_canary.write_text("untouched\n")

        result = _run({"session_id": "../canary"}, isolated_home)

        assert result.returncode == 0
        assert canary.read_text() == "untouched\n"
        assert drift_canary.read_text() == "untouched\n"
