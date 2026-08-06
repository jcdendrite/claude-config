"""Tests for track-permission-prompts.sh.

Notification hook (matcher permission_prompt) that appends the raw
Notification payload — redacted for credential-shaped strings, plus one
added `logged_at` field — to a local, gitignored JSONL log, when the
per-developer sentinel ~/.claude/track-permission-prompts exists. The hook
only logs: it gates nothing, has no deny primitive, and every path exits 0.

Synthetic values used in these tests — all invented, none a real secret:
  ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4  (GitHub classic PAT shape)
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from helpers import HOOKS_DIR, build_path_without

TRACK_HOOK = HOOKS_DIR / "track-permission-prompts.sh"

LOG_FILENAME = ".permission-prompt-log.jsonl"
SENTINEL_FILENAME = "track-permission-prompts"

CREDENTIAL_TOKEN = "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
REDACTED = "[REDACTED-CREDENTIAL]"


def _run_hook(
    payload_stdin: str, tmp_path: Path, extra_env: dict | None = None
) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(tmp_path)}
    env.pop("CLAUDE_CONFIG_DIR", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(TRACK_HOOK)],
        input=payload_stdin,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _notification_payload(**overrides) -> dict:
    payload = {
        "session_id": "test-session-001",
        "hook_event_name": "Notification",
        "message": "Claude needs your permission to run this command.",
    }
    payload.update(overrides)
    return payload


def _enable_sentinel(config_dir: Path) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / SENTINEL_FILENAME).touch()


def _log_path(config_dir: Path) -> Path:
    return config_dir / LOG_FILENAME


def _path_with_jq_that_fails_on_walk(farm_dir: Path) -> str:
    """Build a PATH whose `jq` fails only when invoked with a `walk(`
    filter — i.e. only the redaction call inside
    _lib_redact_credential_shaped_strings, not the hook's other jq calls
    (hook_event_name extraction, logged_at, the final field-merge).
    Isolates a redaction-step-specific failure from a wholesale jq-missing
    failure, which build_path_without("jq", ...) already covers separately.
    """
    farm_dir.mkdir(parents=True, exist_ok=True)
    real_jq = shutil.which("jq")
    wrapper = farm_dir / "jq"
    wrapper.write_text(
        "#!/bin/bash\n"
        'for a in "$@"; do\n'
        "  case \"$a\" in\n"
        "    *walk\\(*) exit 1 ;;\n"
        "  esac\n"
        "done\n"
        f'exec "{real_jq}" "$@"\n'
    )
    wrapper.chmod(0o755)
    return f"{farm_dir}{os.pathsep}{os.environ.get('PATH', '')}"


class TestTrackPermissionPrompts:
    # ------------------------------------------------------------------ #
    # Sentinel gate                                                       #
    # ------------------------------------------------------------------ #

    def test_sentinel_absent_is_no_op(self, tmp_path):
        """Without the sentinel, a matching Notification event exits 0 and
        never creates the log file."""
        config_dir = tmp_path / ".claude"
        config_dir.mkdir()
        result = _run_hook(json.dumps(_notification_payload()), tmp_path)
        assert result.returncode == 0
        assert not _log_path(config_dir).exists()

    def test_sentinel_present_appends_log_line(self, tmp_path):
        """With the sentinel present, a matching Notification event appends
        exactly one line."""
        config_dir = tmp_path / ".claude"
        _enable_sentinel(config_dir)
        result = _run_hook(json.dumps(_notification_payload()), tmp_path)
        assert result.returncode == 0
        log_lines = _log_path(config_dir).read_text().splitlines()
        assert len(log_lines) == 1

    # ------------------------------------------------------------------ #
    # Defense-in-depth: hook_event_name self-check                        #
    # ------------------------------------------------------------------ #

    def test_non_notification_event_is_ignored(self, tmp_path):
        """A non-Notification hook_event_name is ignored even with the
        sentinel present — the defense-in-depth self-check does not rely
        solely on the settings.json matcher."""
        config_dir = tmp_path / ".claude"
        _enable_sentinel(config_dir)
        payload = _notification_payload(hook_event_name="PreToolUse")
        result = _run_hook(json.dumps(payload), tmp_path)
        assert result.returncode == 0
        assert not _log_path(config_dir).exists()

    # ------------------------------------------------------------------ #
    # CLAUDE_CONFIG_DIR resolution                                        #
    # ------------------------------------------------------------------ #

    def test_uses_config_dir_when_set(self, tmp_path):
        """CLAUDE_CONFIG_DIR relocates both the sentinel lookup and the log
        destination away from $HOME/.claude."""
        config_dir = tmp_path / "profile"
        _enable_sentinel(config_dir)
        result = _run_hook(
            json.dumps(_notification_payload()),
            tmp_path,
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        )
        assert result.returncode == 0
        assert _log_path(config_dir).exists()
        assert not _log_path(tmp_path / ".claude").exists()

    def test_relative_config_dir_fails_open(self, tmp_path):
        """A relative CLAUDE_CONFIG_DIR is unresolvable per _lib_config_dir's
        call-site contract; the hook fails open (no log written) rather
        than resolving against an unstated cwd."""
        config_dir = tmp_path / ".claude"
        _enable_sentinel(config_dir)
        result = _run_hook(
            json.dumps(_notification_payload()),
            tmp_path,
            extra_env={"CLAUDE_CONFIG_DIR": "relative/profile"},
        )
        assert result.returncode == 0
        assert not _log_path(config_dir).exists()

    # ------------------------------------------------------------------ #
    # Fail-open on malformed input                                        #
    # ------------------------------------------------------------------ #

    def test_malformed_json_stdin_is_fail_open(self, tmp_path):
        """Non-JSON stdin does not crash the hook or create the log."""
        config_dir = tmp_path / ".claude"
        _enable_sentinel(config_dir)
        result = _run_hook("not valid json {{", tmp_path)
        assert result.returncode == 0
        assert not _log_path(config_dir).exists()

    def test_empty_stdin_is_fail_open(self, tmp_path):
        """Empty stdin exits 0 without creating the log."""
        config_dir = tmp_path / ".claude"
        _enable_sentinel(config_dir)
        result = _run_hook("", tmp_path)
        assert result.returncode == 0
        assert not _log_path(config_dir).exists()

    @pytest.mark.parametrize("non_object_payload", ["[1, 2, 3]", "42", "null", '"just a string"'])
    def test_non_object_top_level_json_is_fail_open(self, tmp_path, non_object_payload):
        """Syntactically valid JSON whose top level isn't an object (array,
        number, null, bare string) fails the `.hook_event_name` extraction
        the same way malformed JSON does -- pins this alongside the
        malformed-JSON case above so a future change to the extraction
        filter can't silently start treating a non-object payload as a
        non-Notification event that proceeds further than intended."""
        config_dir = tmp_path / ".claude"
        _enable_sentinel(config_dir)
        result = _run_hook(non_object_payload, tmp_path)
        assert result.returncode == 0
        assert not _log_path(config_dir).exists()

    # ------------------------------------------------------------------ #
    # Fail-open on a log-write failure                                    #
    # ------------------------------------------------------------------ #

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unwritable_config_dir_does_not_raise_nonzero_exit(self, tmp_path):
        """If the log append fails (e.g. an unwritable config dir), the
        hook still exits 0 — a log-write failure must never surface to the
        user. Asserts the write actually failed (no log file materializes),
        not just that the exit code happens to be 0 on every path including
        success."""
        config_dir = tmp_path / ".claude"
        _enable_sentinel(config_dir)
        config_dir.chmod(0o555)
        try:
            result = _run_hook(json.dumps(_notification_payload()), tmp_path)
            assert result.returncode == 0
            assert not _log_path(config_dir).exists()
        finally:
            config_dir.chmod(0o755)

    # ------------------------------------------------------------------ #
    # Schema-tolerant passthrough                                         #
    # ------------------------------------------------------------------ #

    def test_appended_line_equals_input_plus_logged_at_only(self, tmp_path):
        """The logged line is exactly the input JSON with one added
        `logged_at` field — no other transformation. This is the
        schema-tolerant-passthrough design decision: the hook must not
        assume a field list for the undocumented Notification payload."""
        config_dir = tmp_path / ".claude"
        _enable_sentinel(config_dir)
        payload = _notification_payload(cwd="/tmp/example-workspace", extra_field="untouched")
        result = _run_hook(json.dumps(payload), tmp_path)
        assert result.returncode == 0
        logged = json.loads(_log_path(config_dir).read_text().splitlines()[0])
        logged_at = logged.pop("logged_at")
        assert logged == payload
        # Parses as real ISO 8601, not merely non-empty.
        datetime.fromisoformat(logged_at.replace("Z", "+00:00"))

    # ------------------------------------------------------------------ #
    # Redaction before append (round-1 plan-review finding)               #
    # ------------------------------------------------------------------ #

    def test_credential_shaped_string_is_redacted_before_append(self, tmp_path):
        """A credential-shaped value anywhere in the payload is redacted
        before the line is appended — the test that would have caught
        round-1's missing redaction (raw, unredacted payloads logged to a
        world-readable file)."""
        config_dir = tmp_path / ".claude"
        _enable_sentinel(config_dir)
        payload = _notification_payload(
            message=f"Bash wants to run: curl -H 'Authorization: Bearer {CREDENTIAL_TOKEN}'"
        )
        result = _run_hook(json.dumps(payload), tmp_path)
        assert result.returncode == 0
        log_text = _log_path(config_dir).read_text()
        assert CREDENTIAL_TOKEN not in log_text
        assert REDACTED in log_text

    # ------------------------------------------------------------------ #
    # chmod 600 on every append                                           #
    # ------------------------------------------------------------------ #

    def test_log_file_mode_is_0600_after_append(self, tmp_path):
        """The log file is chmod 600 after every append, undoing the 644
        a plain `>>`-created file would otherwise inherit."""
        config_dir = tmp_path / ".claude"
        _enable_sentinel(config_dir)
        result = _run_hook(json.dumps(_notification_payload()), tmp_path)
        assert result.returncode == 0
        mode = stat.S_IMODE(_log_path(config_dir).stat().st_mode)
        assert mode == 0o600

    def test_chmod_600_tightens_a_pre_existing_looser_mode_log_file(self, tmp_path):
        """chmod 600 also applies when the log file already existed with a
        wider mode — not only on first creation. Guards against a future
        change that moves the chmod inside an "if newly created" branch."""
        config_dir = tmp_path / ".claude"
        _enable_sentinel(config_dir)
        log_path = _log_path(config_dir)
        log_path.write_text("")
        log_path.chmod(0o644)
        result = _run_hook(json.dumps(_notification_payload()), tmp_path)
        assert result.returncode == 0
        mode = stat.S_IMODE(log_path.stat().st_mode)
        assert mode == 0o600

    # ------------------------------------------------------------------ #
    # Repeated invocations append, never overwrite                       #
    # ------------------------------------------------------------------ #

    def test_repeated_invocations_append_not_overwrite(self, tmp_path):
        """Two hook invocations against the same log file leave both lines
        present, in order — a regression from `>>` to `>` would truncate
        prior sessions' entries and go uncaught without this."""
        config_dir = tmp_path / ".claude"
        _enable_sentinel(config_dir)
        first = _run_hook(json.dumps(_notification_payload(session_id="first")), tmp_path)
        second = _run_hook(json.dumps(_notification_payload(session_id="second")), tmp_path)
        assert first.returncode == 0
        assert second.returncode == 0
        log_lines = _log_path(config_dir).read_text().splitlines()
        assert len(log_lines) == 2
        assert json.loads(log_lines[0])["session_id"] == "first"
        assert json.loads(log_lines[1])["session_id"] == "second"

    # ------------------------------------------------------------------ #
    # Redaction failure must not persist unredacted content              #
    # (round-2 code-review finding: fail-open on the redaction step      #
    # alone previously let raw content through to the log)               #
    # ------------------------------------------------------------------ #

    def test_redaction_step_failure_does_not_log_unredacted_content(self, tmp_path):
        """When the redaction call specifically fails (jq present and
        working for every other call, but the walk() call fails), the hook
        must not append the unredacted payload — it must abstain from
        logging entirely, per _lib_redact_credential_shaped_strings's
        fail-closed contract."""
        config_dir = tmp_path / ".claude"
        _enable_sentinel(config_dir)
        farm_dir = tmp_path / "jq-fails-on-walk"
        path_with_broken_jq = _path_with_jq_that_fails_on_walk(farm_dir)
        payload = _notification_payload(
            message=f"Bash wants to run: curl -H 'Authorization: Bearer {CREDENTIAL_TOKEN}'"
        )
        result = _run_hook(
            json.dumps(payload), tmp_path, extra_env={"PATH": path_with_broken_jq}
        )
        assert result.returncode == 0
        assert not _log_path(config_dir).exists()

    def test_oversized_payload_is_fail_open_no_log_written(self, tmp_path):
        """A payload over _LIB_SIZE_THRESHOLD_BYTES is rejected before the
        redaction walk even runs -- mirroring redact-credential-values.sh's
        own size cap, but more conservative: since this hook writes to a
        persistent log rather than passing ephemeral content through, an
        oversized payload is dropped entirely rather than logged unscanned."""
        config_dir = tmp_path / ".claude"
        _enable_sentinel(config_dir)
        oversized_message = "x" * (6 * 1024 * 1024)
        payload = _notification_payload(message=oversized_message)
        result = _run_hook(json.dumps(payload), tmp_path)
        assert result.returncode == 0
        assert not _log_path(config_dir).exists()

    def test_jq_entirely_absent_is_fail_open(self, tmp_path):
        """With jq missing from PATH altogether, every jq-dependent step in
        the hook fails closed and no log line is written — matches this
        suite's established jq-absent convention for sibling hooks."""
        config_dir = tmp_path / ".claude"
        _enable_sentinel(config_dir)
        farm_dir = tmp_path / "no-jq-path"
        farm_dir.mkdir()
        path_without_jq = build_path_without("jq", farm_dir)
        result = _run_hook(
            json.dumps(_notification_payload()), tmp_path, extra_env={"PATH": path_without_jq}
        )
        assert result.returncode == 0
        assert not _log_path(config_dir).exists()

    # ------------------------------------------------------------------ #
    # Non-ASCII round-trip                                               #
    # ------------------------------------------------------------------ #

    def test_non_ascii_message_round_trips_intact(self, tmp_path):
        """A message field containing non-ASCII text survives the jq -c
        round-trip and file append unmangled."""
        config_dir = tmp_path / ".claude"
        _enable_sentinel(config_dir)
        payload = _notification_payload(message="Bash wants to run: cat café-résumé-日本語.txt")
        result = _run_hook(json.dumps(payload), tmp_path)
        assert result.returncode == 0
        logged = json.loads(_log_path(config_dir).read_text().splitlines()[0])
        assert logged["message"] == payload["message"]
