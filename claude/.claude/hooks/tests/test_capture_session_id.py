"""Tests for capture-session-id.sh."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from helpers import HOOKS_DIR

CAPTURE_SESSION_ID_HOOK = HOOKS_DIR / "capture-session-id.sh"


class TestCaptureSessionId:
    """SessionStart hook that bootstraps the session_id ↔ claude-PID
    lookup file. Skill bodies running as Bash tool calls don't see the
    hook payload; they read ~/.claude/sessions/$PPID to learn their own
    session_id (where $PPID is the claude main process PID).

    The hook must never block session startup, so every error path exits 0.
    """

    def _sessions_files(self, home: Path) -> list[Path]:
        sessions_dir = home / ".claude" / "sessions"
        if not sessions_dir.exists():
            return []
        return list(sessions_dir.iterdir())

    def test_valid_input_writes_lookup_file(self, isolated_home):
        sid = "abc-123-session"
        from helpers import run_hook
        run_hook(CAPTURE_SESSION_ID_HOOK, {"session_id": sid})
        files = self._sessions_files(isolated_home)
        assert len(files) == 1, f"expected one lookup file, got {files}"
        assert files[0].read_text().strip() == sid
        # Filename is the claude_pid the hook resolved via `ps -o ppid=`.
        # We don't pin the exact value (depends on test runner topology),
        # but it must be a positive integer.
        assert files[0].name.isdigit() and int(files[0].name) > 0

    def _run_capturing_stderr(self, payload: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(CAPTURE_SESSION_ID_HOOK)],
            input=payload,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_empty_session_id_writes_nothing_with_stderr_diagnostic(self, isolated_home):
        result = self._run_capturing_stderr(json.dumps({"session_id": ""}))
        assert result.returncode == 0
        assert self._sessions_files(isolated_home) == []
        assert "[capture-session-id]" in result.stderr
        assert "no session_id" in result.stderr

    def test_missing_session_id_field_writes_nothing_with_stderr_diagnostic(self, isolated_home):
        result = self._run_capturing_stderr(json.dumps({"some_other_field": "value"}))
        assert result.returncode == 0
        assert self._sessions_files(isolated_home) == []
        assert "[capture-session-id]" in result.stderr
        assert "no session_id" in result.stderr

    def test_empty_stdin_does_not_block_and_emits_stderr(self, isolated_home):
        """Empty payload must not block session start, but must leave a
        diagnostic trail on stderr (not stdout — stdout would pollute
        Claude's context)."""
        result = self._run_capturing_stderr("")
        assert result.returncode == 0
        assert self._sessions_files(isolated_home) == []
        assert "[capture-session-id]" in result.stderr
        assert "empty stdin" in result.stderr
        assert result.stdout == ""

    def test_malformed_json_does_not_block_and_emits_stderr(self, isolated_home):
        """SessionStart hook must never fail-closed on payload corruption —
        a broken hook would prevent the session from starting. Malformed
        JSON is treated as missing session_id."""
        result = self._run_capturing_stderr("not valid json {{")
        assert result.returncode == 0
        assert self._sessions_files(isolated_home) == []
        assert "[capture-session-id]" in result.stderr
        assert result.stdout == ""

    def test_happy_path_emits_no_stderr(self, isolated_home):
        """Successful runs must be silent — stderr noise on every session
        start would condition the user to ignore it."""
        result = self._run_capturing_stderr(json.dumps({"session_id": "abc-123"}))
        assert result.returncode == 0
        assert len(self._sessions_files(isolated_home)) == 1
        assert result.stderr == ""


class TestCaptureSessionIdGC:
    """Garbage-collection sweep inside capture-session-id.sh.

    The hook sweeps ~/.claude/sessions/ at most once per 24 hours
    (throttled via ~/.claude/.sessions-gc-stamp). Each test ensures the
    stamp is absent/stale before invoking the hook so the sweep runs.

    Dead-PID idiom: PID 99999999 is above Linux pid_max and is
    effectively guaranteed to be dead on any test host.
    """

    DEAD_PID = "99999999"
    GC_STAMP_NAME = ".sessions-gc-stamp"

    def _sessions_dir(self, home: Path) -> Path:
        return home / ".claude" / "sessions"

    def _gc_stamp(self, home: Path) -> Path:
        return home / ".claude" / self.GC_STAMP_NAME

    def _seed_session_file(self, home: Path, pid: str, content: str) -> Path:
        """Write a bare-PID lookup file with given content."""
        sessions_dir = self._sessions_dir(home)
        sessions_dir.mkdir(parents=True, exist_ok=True)
        f = sessions_dir / pid
        f.write_text(content + "\n")
        return f

    def _age_file(self, path: Path, seconds_ago: int) -> None:
        """Set a file's mtime to `seconds_ago` seconds in the past."""
        t = time.time() - seconds_ago
        os.utime(path, (t, t))

    def _run(self, session_id: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(CAPTURE_SESSION_ID_HOOK)],
            input=json.dumps({"session_id": session_id}),
            capture_output=True,
            text=True,
            check=False,
        )

    def _ensure_sweep_runs(self, home: Path) -> None:
        """Remove the GC stamp so the sweep is not throttled."""
        stamp = self._gc_stamp(home)
        if stamp.exists():
            stamp.unlink()

    def test_superseded_file_is_deleted(self, isolated_home):
        """A file whose content equals the captured session_id (but with a
        different PID) is a superseded prior incarnation and must be removed."""
        sid = "session-superseded-test"
        stale = self._seed_session_file(isolated_home, "10000001", sid)
        self._age_file(stale, 600)  # 10 minutes old — past the 5-min floor
        self._ensure_sweep_runs(isolated_home)

        result = self._run(sid)

        assert result.returncode == 0
        assert not stale.exists(), "superseded file (same session_id, old PID) must be deleted"

    def test_dead_pid_file_is_deleted(self, isolated_home):
        """A bare-PID file whose PID is dead must be removed."""
        stale = self._seed_session_file(isolated_home, self.DEAD_PID, "some-other-uuid")
        self._age_file(stale, 600)  # 10 minutes old
        self._ensure_sweep_runs(isolated_home)

        result = self._run("current-session-id")

        assert result.returncode == 0
        assert not stale.exists(), "dead-PID file must be deleted"

    def test_live_pid_file_is_kept(self, isolated_home):
        """A bare-PID file for a live process with unrelated content must survive."""
        live_pid = str(os.getpid())
        live_file = self._seed_session_file(isolated_home, live_pid, "unrelated-session-id")
        self._age_file(live_file, 600)  # 10 minutes old
        self._ensure_sweep_runs(isolated_home)

        result = self._run("current-session-id")

        assert result.returncode == 0
        assert live_file.exists(), "live-PID file with unrelated content must be kept"

    def test_json_sidecar_is_never_touched(self, isolated_home):
        """Claude Code's <pid>.json sidecars must never be deleted — the
        basename filter rejects anything that is not purely numeric."""
        sessions_dir = self._sessions_dir(isolated_home)
        sessions_dir.mkdir(parents=True, exist_ok=True)
        sidecar = sessions_dir / f"{self.DEAD_PID}.json"
        sidecar.write_text('{"pid": 99999999, "sessionId": "some-id"}\n')
        self._age_file(sidecar, 600)
        self._ensure_sweep_runs(isolated_home)

        result = self._run("current-session-id")

        assert result.returncode == 0
        assert sidecar.exists(), "<pid>.json sidecar must never be touched by the sweep"

    def test_just_written_file_survives(self, isolated_home):
        """The lookup file the hook writes for this run must never be deleted
        by the sweep (guarded by CLAUDE_PID == this run's PID)."""
        self._ensure_sweep_runs(isolated_home)

        result = self._run("current-session-id")

        assert result.returncode == 0
        sessions_dir = self._sessions_dir(isolated_home)
        assert sessions_dir.exists(), "sessions dir must be created"
        files = list(sessions_dir.iterdir())
        # The hook writes exactly the lookup file for this run; sweep must not delete it.
        lookup_files = [f for f in files if f.name.isdigit()]
        assert len(lookup_files) >= 1, "the just-written lookup file must survive the sweep"

    def test_file_older_than_30_days_with_live_pid_is_deleted(self, isolated_home):
        """A file aged past 30 days with a live PID (own pid, kill -0 passes)
        must be deleted to close the PID-reuse case."""
        live_pid = str(os.getpid())
        old_file = self._seed_session_file(isolated_home, live_pid, "old-session-id")
        # Age to 31 days ago.
        self._age_file(old_file, 31 * 24 * 3600)
        self._ensure_sweep_runs(isolated_home)

        result = self._run("current-session-id")

        assert result.returncode == 0
        assert not old_file.exists(), "file older than 30 days must be deleted even with live PID"

    def test_dead_pid_file_younger_than_5_minutes_is_kept(self, isolated_home):
        """A dead-PID file under 5 minutes old must be kept (conservative
        floor against a just-written sibling file or clock skew)."""
        young_file = self._seed_session_file(isolated_home, self.DEAD_PID, "young-uuid")
        # Age to 2 minutes ago — within the 5-min floor.
        self._age_file(young_file, 120)
        self._ensure_sweep_runs(isolated_home)

        result = self._run("current-session-id")

        assert result.returncode == 0
        assert young_file.exists(), "dead-PID file under 5 minutes old must be kept"

    def test_throttle_suppresses_sweep_within_24h(self, isolated_home):
        """When ~/.claude/.sessions-gc-stamp has a fresh mtime, the sweep
        must be skipped entirely, leaving a seeded dead-PID file untouched."""
        stale = self._seed_session_file(isolated_home, self.DEAD_PID, "should-survive")
        self._age_file(stale, 600)  # 10 minutes old — would be deleted without throttle

        # Write a fresh stamp (mtime now).
        stamp = self._gc_stamp(isolated_home)
        stamp.touch()

        result = self._run("current-session-id")

        assert result.returncode == 0
        assert stale.exists(), "throttle must prevent sweep — dead-PID file must survive"

    def test_hook_exits_0_in_all_gc_scenarios(self, isolated_home):
        """Hook must exit 0 even when the sessions directory is populated
        with a mix of stale, live, and sidecar files."""
        sessions_dir = self._sessions_dir(isolated_home)
        sessions_dir.mkdir(parents=True, exist_ok=True)
        self._seed_session_file(isolated_home, self.DEAD_PID, "dead-session")
        self._age_file(sessions_dir / self.DEAD_PID, 600)
        (sessions_dir / f"{self.DEAD_PID}.json").write_text('{"pid":99999999}\n')
        self._ensure_sweep_runs(isolated_home)

        result = self._run("current-session-id")

        assert result.returncode == 0
