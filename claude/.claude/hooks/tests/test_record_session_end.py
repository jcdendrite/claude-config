"""Tests for record-session-end.sh."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from helpers import (
    CANARY_CONTENT,
    HOOKS_DIR,
    TRAVERSAL_SESSION_ID,
    plant_traversal_canary,
    run_hook_advisory,
)

RECORD_SESSION_END_HOOK = HOOKS_DIR / "record-session-end.sh"


class TestRecordSessionEnd:
    """SessionEnd hook that records a graceful shutdown, one file per Claude
    process, so post-crash-sessions.py can tell a deliberate clean exit apart
    from a crash once the process is dead.

    The hook must never fail closed, so every error path exits 0.
    """

    def _records_dir(self, home: Path) -> Path:
        return home / ".claude" / "session-end-records"

    def _records_files(self, home: Path) -> list[Path]:
        records_dir = self._records_dir(home)
        if not records_dir.exists():
            return []
        return list(records_dir.iterdir())

    def _run_capturing_stderr(
        self, payload: str, extra_env: dict | None = None
    ) -> subprocess.CompletedProcess:
        env = {**os.environ, **extra_env} if extra_env else None
        return subprocess.run(
            [str(RECORD_SESSION_END_HOOK)],
            input=payload,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    def test_valid_input_writes_record(self, isolated_home):
        sid = "abc-123-session"
        decision = run_hook_advisory(
            RECORD_SESSION_END_HOOK, {"session_id": sid, "reason": "prompt_input_exit"}
        )
        assert decision == "allow"
        files = self._records_files(isolated_home)
        assert len(files) == 1, f"expected one record file, got {files}"
        # Keyed by claude-pid, the same way capture-session-id.sh keys its own
        # lookup file. The hook is invoked with no shell (subprocess.run([...])),
        # so its own $PPID is this pytest process.
        assert files[0].name == str(os.getpid())
        record = json.loads(files[0].read_text())
        assert record == {"sessionId": sid, "reason": "prompt_input_exit"}

    def test_claude_pid_env_resolves_record_filename_not_just_ppid(self, isolated_home):
        """Mirrors test_capture_session_id.py's shim-parent case: running the
        hook as a child of `sh` (not `exec`, which would collapse the hop and
        make $PPID equal $CLAUDE_PID trivially) makes the hook's own $PPID
        the shell's pid and $CLAUDE_PID (pytest's own pid, the shell's
        parent) the accepted one-hop candidate. Every other test in this
        file runs with $CLAUDE_PID cleared by the autouse fixture, so this
        is the only case exercising the hook's wiring to the shared
        _lib_hook_claude_pid helper rather than always resolving through
        the unset-$CLAUDE_PID default ($PPID) path."""
        sid = "claude-pid-shim-session"
        payload = json.dumps({"session_id": sid})
        env = {**os.environ, "HOME": str(isolated_home), "CLAUDE_PID": str(os.getpid())}
        result = subprocess.run(
            ["sh", "-c", f'"{RECORD_SESSION_END_HOOK}"; true'],
            input=payload,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert result.returncode == 0
        files = self._records_files(isolated_home)
        assert len(files) == 1, f"expected one record file, got {files}"
        assert files[0].name == str(os.getpid()), (
            "must land at the $CLAUDE_PID candidate (pytest's own pid), not the shim's $PPID"
        )

    def test_reason_with_shell_metacharacters_and_long_value_round_trips_as_literal_string(
        self, isolated_home
    ):
        """`reason` is parameterized into `jq -n --arg`, unlike session_id's
        allowlist-guarded path -- this is the adversarial-input coverage for
        that different code path. A reason containing quotes, backticks, a
        $(...) command-substitution shape, an embedded newline, and a
        pathologically long value must round-trip intact as a literal
        string, prove no shell execution occurred, and still produce
        exactly one valid JSON document, not truncated or trailing-garbage
        output."""
        sid = "adversarial-reason-session"
        reason = "quote' \"dquote\" `id` $(id) \n embedded-newline " + "x" * 100_000
        decision = run_hook_advisory(
            RECORD_SESSION_END_HOOK, {"session_id": sid, "reason": reason}
        )
        assert decision == "allow"
        files = self._records_files(isolated_home)
        assert len(files) == 1, f"expected one record file, got {files}"
        text = files[0].read_text()
        record, end_index = json.JSONDecoder().raw_decode(text)
        assert text[end_index:].strip() == "", (
            "record file must hold exactly one JSON document, not extra trailing content"
        )
        assert record == {"sessionId": sid, "reason": reason}

    def test_missing_reason_still_writes_record(self, isolated_home):
        """`reason` is absent from the payload entirely (e.g. a documented
        `SessionEnd` shape this hook hasn't seen yet) -- the record must
        still be written, with `reason` coerced to JSON null rather than the
        write being skipped."""
        sid = "no-reason-session"
        run_hook_advisory(RECORD_SESSION_END_HOOK, {"session_id": sid})
        files = self._records_files(isolated_home)
        assert len(files) == 1, f"expected one record file, got {files}"
        record = json.loads(files[0].read_text())
        assert record == {"sessionId": sid, "reason": None}

    def test_empty_stdin_does_not_block_and_emits_stderr(self, isolated_home):
        result = self._run_capturing_stderr("")
        assert result.returncode == 0
        assert self._records_files(isolated_home) == []
        assert "[record-session-end]" in result.stderr
        assert "empty stdin" in result.stderr
        assert result.stdout == ""

    def test_malformed_json_does_not_block_and_emits_stderr(self, isolated_home):
        """A SessionEnd hook must never fail-closed on payload corruption.
        Malformed JSON is treated as missing session_id."""
        result = self._run_capturing_stderr("not valid json {{")
        assert result.returncode == 0
        assert self._records_files(isolated_home) == []
        assert "[record-session-end]" in result.stderr
        assert result.stdout == ""

    def test_missing_session_id_field_writes_nothing_with_stderr_diagnostic(self, isolated_home):
        result = self._run_capturing_stderr(json.dumps({"reason": "clear"}))
        assert result.returncode == 0
        assert self._records_files(isolated_home) == []
        assert "[record-session-end]" in result.stderr
        assert "no session_id" in result.stderr

    def test_empty_session_id_writes_nothing_with_stderr_diagnostic(self, isolated_home):
        result = self._run_capturing_stderr(json.dumps({"session_id": "", "reason": "clear"}))
        assert result.returncode == 0
        assert self._records_files(isolated_home) == []
        assert "[record-session-end]" in result.stderr
        assert "no session_id" in result.stderr

    def test_traversal_session_id_writes_nothing_and_leaves_canary_untouched(
        self, isolated_home
    ):
        """The record file is keyed by claude-pid, not session_id, so
        session_id never becomes a path component here -- but the hook still
        validates it (defense-in-depth for the record's downstream reader,
        post-crash-sessions.py) and must write nothing for a traversal-shaped
        value."""
        canary = plant_traversal_canary(isolated_home)

        result = self._run_capturing_stderr(
            json.dumps({"session_id": TRAVERSAL_SESSION_ID})
        )

        assert result.returncode == 0
        assert canary.read_text() == CANARY_CONTENT
        assert self._records_files(isolated_home) == []
        assert "[record-session-end]" in result.stderr
        assert "not a valid path component" in result.stderr

    def test_uses_config_dir_records_when_set(self, isolated_home, tmp_path):
        """CLAUDE_CONFIG_DIR replaces the whole ~/.claude directory, not just
        $HOME -- the record lands under $CLAUDE_CONFIG_DIR/session-end-records,
        not $HOME/.claude/session-end-records."""
        config_dir = tmp_path / "profile"
        config_dir.mkdir()
        sid = "config-dir-session"
        run_hook_advisory(
            RECORD_SESSION_END_HOOK,
            {"session_id": sid},
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        )
        assert self._records_files(isolated_home) == []
        files = list((config_dir / "session-end-records").iterdir())
        assert len(files) == 1, f"expected one record file under CLAUDE_CONFIG_DIR, got {files}"
        record = json.loads(files[0].read_text())
        assert record["sessionId"] == sid

    def test_sweep_deletes_stale_record_and_preserves_fresh_one(self, isolated_home):
        records_dir = self._records_dir(isolated_home)
        records_dir.mkdir(parents=True)
        stale = records_dir / "99999"
        stale.write_text('{"sessionId": "old", "reason": null}\n')
        old_time = time.time() - 31 * 86400
        os.utime(stale, (old_time, old_time))
        fresh = records_dir / "88888"
        fresh.write_text('{"sessionId": "fresh", "reason": null}\n')

        run_hook_advisory(RECORD_SESSION_END_HOOK, {"session_id": "sweep-session"})

        remaining = {f.name for f in self._records_files(isolated_home)}
        assert "99999" not in remaining, "a >30-day-old record must be swept"
        assert "88888" in remaining, "a fresh record must survive the sweep"
        assert str(os.getpid()) in remaining, "this fire's own record must be written"

    def test_sweep_runs_after_write_so_record_survives_an_unsweepable_directory(
        self, isolated_home, tmp_path
    ):
        """If the sweep itself fails outright, the record already written
        just before it must still be there -- self-sweep can never cost the
        write that already succeeded. Forced by shadowing `find` on PATH
        with a stub that always fails."""
        stub_dir = tmp_path / "find-stub"
        stub_dir.mkdir()
        stub_find = stub_dir / "find"
        stub_find.write_text("#!/bin/bash\nexit 1\n")
        stub_find.chmod(0o755)

        result = self._run_capturing_stderr(
            json.dumps({"session_id": "sweep-failure-session"}),
            extra_env={"PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}"},
        )

        assert result.returncode == 0
        files = self._records_files(isolated_home)
        assert len(files) == 1, f"expected the just-written record to survive, got {files}"
        assert files[0].name == str(os.getpid())

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unwritable_records_parent_writes_nothing_and_exits_zero(self, isolated_home):
        """mkdir -p (or the record write itself) failing -- simulated via a
        read-only config dir -- must degrade to no record, not a stray
        error."""
        config_dir = isolated_home / ".claude"
        config_dir.chmod(0o555)
        try:
            result = self._run_capturing_stderr(json.dumps({"session_id": "unwritable-session"}))
            assert result.returncode == 0
            assert self._records_files(isolated_home) == []
            assert "[record-session-end]" in result.stderr
        finally:
            config_dir.chmod(0o755)

    @pytest.mark.parametrize(
        "payload",
        [
            "",
            "not valid json {{",
            json.dumps({"reason": "clear"}),
            json.dumps({"session_id": ""}),
            json.dumps({"session_id": TRAVERSAL_SESSION_ID}),
            json.dumps({"session_id": "exit-status-check-session"}),
        ],
    )
    def test_exit_status_is_zero_on_every_path(self, isolated_home, payload):
        result = self._run_capturing_stderr(payload)
        assert result.returncode == 0, f"payload {payload!r} produced exit {result.returncode}"
