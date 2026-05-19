"""Tests for cleanup-session-id.sh."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from helpers import HOOKS_DIR

CAPTURE_SESSION_ID_HOOK = HOOKS_DIR / "capture-session-id.sh"
CLEANUP_SESSION_ID_HOOK = HOOKS_DIR / "cleanup-session-id.sh"


class TestCleanupSessionId:
    """SessionEnd hook that deletes the session_id ↔ claude-PID lookup
    file capture-session-id.sh wrote. It is the destructor that hook
    never had; without it ~/.claude/sessions/ grows unbounded.

    A content-match guard makes the /clear race correct in both
    orderings: /clear ends one session and starts another in the same
    claude process (same PID, new session_id), and the file is deleted
    only when its content still equals the *ending* session's id.

    The hook must never block session teardown, so every path exits 0.

    Tests pair the two hooks the way production does — run
    capture-session-id.sh first to write sessions/<pid>, then run
    cleanup-session-id.sh. Both resolve the *same* claude-PID from the
    same `ps -o ppid=` walk, so the test never has to predict the PID.
    """

    def _sessions_dir(self, home: Path) -> Path:
        return home / ".claude" / "sessions"

    def _sessions_files(self, home: Path) -> list[Path]:
        sessions_dir = self._sessions_dir(home)
        if not sessions_dir.exists():
            return []
        return list(sessions_dir.iterdir())

    def _run(self, hook: Path, payload: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(hook)],
            input=payload,
            capture_output=True,
            text=True,
            check=False,
        )

    def _capture(self, session_id: str) -> subprocess.CompletedProcess:
        return self._run(
            CAPTURE_SESSION_ID_HOOK, json.dumps({"session_id": session_id})
        )

    def _cleanup(self, session_id: str | None) -> subprocess.CompletedProcess:
        payload = {} if session_id is None else {"session_id": session_id}
        return self._run(CLEANUP_SESSION_ID_HOOK, json.dumps(payload))

    def test_cleanup_deletes_file_when_session_id_matches(self, isolated_home):
        """capture then cleanup with the same session_id — the lookup file
        capture wrote is removed by its paired destructor."""
        sid = "match-1-session"
        self._capture(sid)
        assert len(self._sessions_files(isolated_home)) == 1
        result = self._cleanup(sid)
        assert result.returncode == 0
        assert self._sessions_files(isolated_home) == []
        assert result.stderr == ""

    def test_cleanup_keeps_file_when_session_id_differs(self, isolated_home):
        """capture with id A, cleanup with id B — the content-match guard
        keeps the file (content A). Models the /clear ordering where
        SessionStart rewrites the file before the old session's
        SessionEnd runs: the file now belongs to the live successor."""
        self._capture("session-A")
        result = self._cleanup("session-B")
        assert result.returncode == 0
        files = self._sessions_files(isolated_home)
        assert len(files) == 1
        assert files[0].read_text().strip() == "session-A"
        assert result.stderr == ""

    def test_cleanup_no_lookup_file_present(self, isolated_home):
        """No sessions/<pid> file at all — cleanup exits 0, no error."""
        result = self._cleanup("orphan-session")
        assert result.returncode == 0
        assert self._sessions_files(isolated_home) == []

    def test_cleanup_empty_stdin_exits_zero(self, isolated_home):
        """Empty payload must not break session teardown."""
        result = self._run(CLEANUP_SESSION_ID_HOOK, "")
        assert result.returncode == 0
        assert result.stderr == ""

    def test_cleanup_missing_session_id_exits_zero(self, isolated_home):
        """Payload with no session_id field — exits 0, stays silent."""
        result = self._run(CLEANUP_SESSION_ID_HOOK, json.dumps({"other": "x"}))
        assert result.returncode == 0
        assert result.stderr == ""

    def test_cleanup_malformed_json_exits_zero(self, isolated_home):
        """Payload corruption is treated as missing session_id — a
        SessionEnd hook must never fail-closed."""
        result = self._run(CLEANUP_SESSION_ID_HOOK, "not valid json {{")
        assert result.returncode == 0
        assert self._sessions_files(isolated_home) == []

    def test_cleanup_preserves_pid_json_sidecar(self, isolated_home):
        """The hook deletes a single exact path (sessions/<pid>) and never
        globs the directory, so the <pid>.json sidecar for the very PID
        being cleaned survives."""
        sid = "sidecar-session"
        self._capture(sid)
        files = self._sessions_files(isolated_home)
        assert len(files) == 1
        pid = files[0].name
        sidecar = self._sessions_dir(isolated_home) / f"{pid}.json"
        sidecar.write_text('{"pid": 1, "sessionId": "x"}\n')
        result = self._cleanup(sid)
        assert result.returncode == 0
        assert sidecar.exists()
        assert not (self._sessions_dir(isolated_home) / pid).exists()
