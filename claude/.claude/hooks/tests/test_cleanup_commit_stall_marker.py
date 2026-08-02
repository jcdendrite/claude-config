"""Tests for cleanup-commit-stall-marker.sh.

SessionEnd destructor for advance-past-commit-stall.sh's per-session state
file (~/.claude/.commit-stall-block.d/<session_id>), mirroring
cleanup-worktree-anchor-nudge-marker.sh's shape. Also covers the 30-day sweep
this destructor has that the other cleanup hooks lack — `claude -p` one-shot
runs never fire SessionEnd, so state files from those runs would otherwise
accumulate without bound.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from helpers import (
    CANARY_CONTENT,
    HOOKS_DIR,
    TRAVERSAL_SESSION_ID,
    plant_traversal_canary,
)

CLEANUP_HOOK = HOOKS_DIR / "cleanup-commit-stall-marker.sh"


def _state_dir(home: Path) -> Path:
    return home / ".claude" / ".commit-stall-block.d"


def _run(payload: dict | None, home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(CLEANUP_HOOK)],
        input=json.dumps(payload) if payload is not None else "",
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True,
        check=False,
    )


def _touch_with_age(path: Path, days_old: int) -> None:
    path.write_text("p1\n")
    stamp = time.time() - (days_old * 86400)
    os.utime(path, (stamp, stamp))


class TestCleanupCommitStallMarker:
    def test_deletes_the_state_file_for_this_session(self, isolated_home):
        state_dir = _state_dir(isolated_home)
        state_dir.mkdir(parents=True)
        (state_dir / "session-a").write_text("p1\n")

        result = _run({"session_id": "session-a"}, isolated_home)

        assert result.returncode == 0
        assert not (state_dir / "session-a").exists()

    def test_leaves_other_sessions_state_files_untouched(self, isolated_home):
        state_dir = _state_dir(isolated_home)
        state_dir.mkdir(parents=True)
        (state_dir / "session-a").write_text("p1\n")
        (state_dir / "session-b").write_text("p2\n")

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

    def test_missing_lib_sh_exits_zero_without_deleting(self, isolated_home):
        """Fail-open inversion of GATE_HOOKS's test_missing_lib_sh_denied: this
        informational hook must exit 0 (not error) when _lib.sh is absent, and
        since the source happens before any state-file access, the session's
        state file must survive untouched rather than being silently deleted
        via some other path.

        The hook is COPIED (not symlinked) into a temp directory so that
        dirname($0) resolves to the temp dir, where _lib.sh is genuinely absent.

        Same caveat test_missing_lib_sh_denied documents for itself: this cannot
        structurally distinguish "exited silently because _lib.sh's own guard
        fired" from "exited silently because the downstream, now-undefined
        _lib_valid_session_id_component call failed and its own `|| exit 0`
        caught it." This test pins the observable behavior (silent exit,
        state file untouched, under a missing _lib.sh), not the specific
        guard line.
        """
        state_dir = _state_dir(isolated_home)
        state_dir.mkdir(parents=True)
        (state_dir / "session-a").write_text("p1\n")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_hook = Path(tmpdir) / CLEANUP_HOOK.name
            shutil.copy2(CLEANUP_HOOK, tmp_hook)
            tmp_hook.chmod(0o755)
            result = subprocess.run(
                [str(tmp_hook)],
                input=json.dumps({"session_id": "session-a"}),
                env={**os.environ, "HOME": str(isolated_home)},
                capture_output=True,
                text=True,
                check=False,
            )

        assert result.returncode == 0
        assert (state_dir / "session-a").exists(), (
            "missing _lib.sh must fail open before touching the state file"
        )

    def test_traversal_session_id_does_not_delete_files_outside_state_dir(
        self, isolated_home
    ):
        canary = plant_traversal_canary(isolated_home)

        result = _run({"session_id": TRAVERSAL_SESSION_ID}, isolated_home)

        assert result.returncode == 0
        assert canary.read_text() == CANARY_CONTENT

    # -- 30-day sweep, absent from the destructors this one is modeled on --

    def test_sweep_deletes_entries_older_than_30_days(self, isolated_home):
        state_dir = _state_dir(isolated_home)
        state_dir.mkdir(parents=True)
        stale = state_dir / "old-session"
        _touch_with_age(stale, days_old=31)

        result = _run({"session_id": "unrelated-session"}, isolated_home)

        assert result.returncode == 0
        assert not stale.exists(), "entries older than 30 days must be swept"

    def test_sweep_preserves_entries_within_30_days(self, isolated_home):
        state_dir = _state_dir(isolated_home)
        state_dir.mkdir(parents=True)
        fresh = state_dir / "recent-session"
        _touch_with_age(fresh, days_old=1)

        result = _run({"session_id": "unrelated-session"}, isolated_home)

        assert result.returncode == 0
        assert fresh.exists(), "entries within 30 days must survive the sweep"

    def test_sweep_confined_to_state_dir_when_absent(self, isolated_home):
        """No state dir at all — the destructor's own session-file rm -f and
        the sweep's `[ -d ]` guard must both no-op, not create the dir."""
        state_dir = _state_dir(isolated_home)
        assert not state_dir.exists()

        result = _run({"session_id": "session-a"}, isolated_home)

        assert result.returncode == 0

    def test_sweep_skipped_when_state_dir_is_a_symlink(self, isolated_home, tmp_path):
        """A symlinked state dir must not be followed by the sweep — the
        `[ ! -L ]` guard keeps `find -delete` from reaching through it."""
        real_target = tmp_path / "elsewhere"
        real_target.mkdir()
        stale_outside = real_target / "old-session"
        _touch_with_age(stale_outside, days_old=31)

        state_dir = _state_dir(isolated_home)
        state_dir.parent.mkdir(parents=True, exist_ok=True)
        state_dir.symlink_to(real_target)

        result = _run({"session_id": "session-a"}, isolated_home)

        assert result.returncode == 0
        assert stale_outside.exists(), (
            "sweep must not follow a symlinked state dir into another directory"
        )
