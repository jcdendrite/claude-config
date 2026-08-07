"""Tests for capture-session-id.sh."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from helpers import (
    CANARY_CONTENT,
    HOOKS_DIR,
    TRAVERSAL_SESSION_ID,
    plant_traversal_canary,
)

CAPTURE_SESSION_ID_HOOK = HOOKS_DIR / "capture-session-id.sh"


class TestCaptureSessionId:
    """SessionStart hook that bootstraps the session_id ↔ claude-PID
    lookup file. Skill bodies running as Bash tool calls don't see the
    hook payload; they read sessions/$PPID under the resolved config
    directory to learn their own session_id (where $PPID is the claude
    main process PID).

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
        # Two-line format: session id, then the resolved claude PID's
        # `ps -o lstart=` start time -- not just the bare session id.
        lines = files[0].read_text().split("\n")
        assert lines[0] == sid
        assert lines[1] != "", "second line must hold the resolved process start time"
        # The hook is invoked with no shell (subprocess.run([str(hook)])), so
        # its own $PPID is this pytest process -- pin the exact value rather
        # than only checking it's a positive integer, or a hop-count error in
        # the derivation passes silently.
        assert files[0].name == str(os.getpid())

    def test_claude_pid_env_equal_to_ppid_is_accepted(self, isolated_home):
        """The one-hop bound has two accept disjuncts: $CLAUDE_PID equals
        $PPID exactly (no shim), or equals $PPID's immediate parent (shim
        case, covered by test_claude_pid_env_takes_precedence_over_ppid
        below). This is the no-shim disjunct's only dedicated coverage --
        without it, test_valid_input_writes_lookup_file only ever exercises
        the unset-$CLAUDE_PID default path, never this explicit-match branch,
        and a broken comparison here (wrong operator, wrong variable) would
        pass the full suite silently. This is plausibly the dominant real
        invocation shape: Claude Code exports $CLAUDE_PID unconditionally per
        this hook's own header comment, and settings.json registers this hook
        with no intervening shim."""
        from helpers import run_hook

        sid = "self-equal-claude-pid-session"
        run_hook(
            CAPTURE_SESSION_ID_HOOK,
            {"session_id": sid},
            extra_env={"CLAUDE_PID": str(os.getpid())},
        )
        files = self._sessions_files(isolated_home)
        assert len(files) == 1, f"expected one lookup file, got {files}"
        assert files[0].name == str(os.getpid())

    def test_claude_pid_env_takes_precedence_over_ppid(self, isolated_home, tmp_path):
        """$CLAUDE_PID is accepted when it differs from the hook's own $PPID,
        as long as it's $PPID's immediate parent -- the shim case the
        derivation exists for. Running the hook as a genuine child of `sh`
        (not `exec`, which would collapse the hop and make $PPID equal
        $CLAUDE_PID trivially) makes the hook's $PPID the shim's pid and
        $CLAUDE_PID (pytest's own pid) the shim's parent. This is the only
        way to observe the two candidates actually differing;
        test_valid_input_writes_lookup_file alone would pass even if the
        $CLAUDE_PID read were deleted outright, since under the autouse
        fixture the two candidates are identical there."""
        sid = "shim-session"
        payload = json.dumps({"session_id": sid})
        env = {**os.environ, "HOME": str(isolated_home), "CLAUDE_PID": str(os.getpid())}
        result = subprocess.run(
            ["sh", "-c", f'"{CAPTURE_SESSION_ID_HOOK}"; true'],
            input=payload,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert result.returncode == 0
        files = self._sessions_files(isolated_home)
        assert len(files) == 1, f"expected one lookup file, got {files}"
        assert files[0].name == str(os.getpid()), (
            "must land at the $CLAUDE_PID candidate (pytest's own pid), not the shim's $PPID"
        )

    def test_non_numeric_claude_pid_falls_back_to_ppid(self, isolated_home):
        """A non-numeric $CLAUDE_PID must fall back to $PPID and still write
        a file -- not become a path component, and not abort the write."""
        from helpers import run_hook

        sid = "non-numeric-claude-pid-session"
        run_hook(CAPTURE_SESSION_ID_HOOK, {"session_id": sid}, extra_env={"CLAUDE_PID": "abc"})
        files = self._sessions_files(isolated_home)
        assert len(files) == 1, f"expected one lookup file, got {files}"
        assert files[0].name == str(os.getpid())

    def test_whitespace_claude_pid_falls_back_to_ppid(self, isolated_home):
        """Validate-then-select: ${CLAUDE_PID:-$PPID}-style substitution would
        select a whitespace-only value before any check runs, since :- only
        fires on unset-or-empty. That must not happen here -- whitespace
        must fail the numeric check and fall back to $PPID."""
        from helpers import run_hook

        sid = "whitespace-claude-pid-session"
        run_hook(CAPTURE_SESSION_ID_HOOK, {"session_id": sid}, extra_env={"CLAUDE_PID": "   "})
        files = self._sessions_files(isolated_home)
        assert len(files) == 1, f"expected one lookup file, got {files}"
        assert files[0].name == str(os.getpid())

    def test_live_non_ancestor_claude_pid_is_rejected(self, isolated_home):
        """A numeric, live $CLAUDE_PID outside the one-hop bound (not $PPID,
        not $PPID's immediate parent) must be rejected -- the sole invariant
        the one-hop design exists to enforce. A `sleep` child of pytest is
        live but not an ancestor of the hook process at all."""
        from helpers import run_hook

        sleeper = subprocess.Popen(["sleep", "30"])
        try:
            sid = "non-ancestor-claude-pid-session"
            run_hook(
                CAPTURE_SESSION_ID_HOOK,
                {"session_id": sid},
                extra_env={"CLAUDE_PID": str(sleeper.pid)},
            )
            files = self._sessions_files(isolated_home)
            assert len(files) == 1, f"expected one lookup file, got {files}"
            assert files[0].name == str(os.getpid()), (
                "a live non-ancestor CLAUDE_PID must be rejected, falling back to $PPID"
            )
        finally:
            sleeper.terminate()
            sleeper.wait()

    def _run_capturing_stderr(
        self, payload: str, extra_env: dict | None = None
    ) -> subprocess.CompletedProcess:
        env = {**os.environ, **extra_env} if extra_env else None
        return subprocess.run(
            [str(CAPTURE_SESSION_ID_HOOK)],
            input=payload,
            capture_output=True,
            text=True,
            check=False,
            env=env,
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

    def test_uses_config_dir_sessions_when_set(self, isolated_home, tmp_path):
        """CLAUDE_CONFIG_DIR replaces the whole ~/.claude directory, not just
        $HOME (https://code.claude.com/docs/en/claude-directory) -- the
        lookup file lands under $CLAUDE_CONFIG_DIR/sessions, not
        $HOME/.claude/sessions."""
        from helpers import run_hook
        config_dir = tmp_path / "profile"
        config_dir.mkdir()
        sid = "config-dir-session"
        run_hook(
            CAPTURE_SESSION_ID_HOOK,
            {"session_id": sid},
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        )
        assert self._sessions_files(isolated_home) == []
        files = list((config_dir / "sessions").iterdir())
        assert len(files) == 1, f"expected one lookup file under CLAUDE_CONFIG_DIR, got {files}"
        lines = files[0].read_text().split("\n")
        assert lines[0] == sid

    def test_relative_config_dir_fails_open_with_stderr_diagnostic(self, isolated_home):
        """A relative CLAUDE_CONFIG_DIR is unresolvable per _lib_config_dir's
        call-site contract and must not silently collapse to a root-anchored
        write -- the hook fails open, writing nothing."""
        result = subprocess.run(
            [str(CAPTURE_SESSION_ID_HOOK)],
            input=json.dumps({"session_id": "rel-config-dir-session"}),
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "CLAUDE_CONFIG_DIR": "relative/path"},
        )
        assert result.returncode == 0
        assert self._sessions_files(isolated_home) == []
        assert "[capture-session-id]" in result.stderr
        assert "could not resolve config dir" in result.stderr

    def test_happy_path_emits_no_stderr(self, isolated_home):
        """Successful runs must be silent — stderr noise on every session
        start would condition the user to ignore it."""
        result = self._run_capturing_stderr(json.dumps({"session_id": "abc-123"}))
        assert result.returncode == 0
        assert len(self._sessions_files(isolated_home)) == 1
        assert result.stderr == ""

    def test_pid_json_sidecar_survives_repeated_capture(self, isolated_home):
        """~/.claude/sessions is co-owned with Claude Code, which writes
        <pid>.json sidecars there. This hook writes only its own exact
        <pid> lookup path (never a glob), so a sidecar for that same pid
        must survive a second SessionStart in the same process tree."""
        from helpers import run_hook

        run_hook(CAPTURE_SESSION_ID_HOOK, {"session_id": "sidecar-session-1"})
        files = self._sessions_files(isolated_home)
        assert len(files) == 1
        pid = files[0].name
        sidecar = isolated_home / ".claude" / "sessions" / f"{pid}.json"
        sidecar.write_text('{"pid": 1, "sessionId": "x"}\n')

        run_hook(CAPTURE_SESSION_ID_HOOK, {"session_id": "sidecar-session-2"})

        assert sidecar.read_text() == '{"pid": 1, "sessionId": "x"}\n'

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
        canary = plant_traversal_canary(isolated_home)

        result = self._run_capturing_stderr(
            json.dumps({"session_id": TRAVERSAL_SESSION_ID})
        )

        assert result.returncode == 0
        assert canary.read_text() == CANARY_CONTENT
        assert self._sessions_files(isolated_home) == []
        assert "[capture-session-id]" in result.stderr
        assert "not a valid path component" in result.stderr

    def test_unresolvable_start_time_does_not_block_and_emits_stderr(
        self, isolated_home, tmp_path
    ):
        """CLAUDE_PID_START's resolution failure must fail open like every
        other step in this hook: no lookup file written, session startup not
        blocked, and a stderr diagnostic naming the branch. Forced by
        shadowing `ps` on PATH with a stub that fails only the `-o lstart=`
        invocation; $CLAUDE_PID is unset here (autouse fixture), so the
        derivation makes no `-o ppid=` call at all and resolves $PPID
        directly, isolating this failure to the new branch."""
        real_ps = shutil.which("ps")
        assert real_ps, "ps must be resolvable to build a stub that shadows it"
        stub_dir = tmp_path / "ps-stub"
        stub_dir.mkdir()
        stub_ps = stub_dir / "ps"
        stub_ps.write_text(
            "#!/bin/bash\n"
            'for arg in "$@"; do\n'
            '  [ "$arg" = "lstart=" ] && exit 1\n'
            "done\n"
            f'exec "{real_ps}" "$@"\n'
        )
        stub_ps.chmod(0o755)

        result = self._run_capturing_stderr(
            json.dumps({"session_id": "start-time-failure-session"}),
            extra_env={"PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}"},
        )

        assert result.returncode == 0
        assert self._sessions_files(isolated_home) == []
        assert "[capture-session-id]" in result.stderr
        assert "could not resolve start time" in result.stderr
