"""Tests for capture-session-id.sh."""
from __future__ import annotations

import json
import subprocess
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

    def test_traversal_session_id_does_not_overwrite_active_marker_canary(
        self, isolated_home
    ):
        """The lookup file (sessions/$CLAUDE_PID) is keyed by claude_pid, not
        session_id, so it isn't this hook's traversal sink. The active.d
        rewrite loop is: it builds `$_active_dir/$SESSION_ID` directly from
        the payload and, when that path already exists, overwrites it with
        the resolved claude_pid — an escape here truncates and rewrites an
        attacker-chosen file. A session_id of '../canary' with
        .plan-review-active.d present must not touch a file living one
        level up, in ~/.claude directly."""
        active_dir = isolated_home / ".claude" / ".plan-review-active.d"
        active_dir.mkdir(parents=True)
        canary = isolated_home / ".claude" / "canary"
        canary.write_text("untouched\n")

        result = self._run_capturing_stderr(json.dumps({"session_id": "../canary"}))

        assert result.returncode == 0
        assert canary.read_text() == "untouched\n"
        assert self._sessions_files(isolated_home) == []
        assert "[capture-session-id]" in result.stderr
        assert "not a valid path component" in result.stderr
