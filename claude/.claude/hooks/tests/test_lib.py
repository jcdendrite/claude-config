"""Unit tests for _lib_parse_tool_input_or_deny and _lib_jq in _lib.sh.

Each test drives the helpers via a throwaway shell harness that defines
emit_deny before sourcing _lib.sh (the canonical caller pattern), then calls
_lib_parse_tool_input_or_deny and reports either DENY:<msg> or OK:<tool>:<cmd>.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

# Path to _lib.sh: test lives in hooks/tests/, _lib.sh is in hooks/.
_LIB_SH = Path(__file__).resolve().parents[1] / "_lib.sh"

# Shell harness: define emit_deny BEFORE sourcing _lib.sh (canonical pattern),
# call the helper, then print OK:<TOOL_NAME>:<COMMAND> on success.
# {lib} is substituted by the test with the absolute path to _lib.sh.
_HARNESS_TEMPLATE = (
    'emit_deny() {{ printf "DENY:%s\\n" "$1"; exit 0; }}; '
    ". {lib}; "
    '_lib_parse_tool_input_or_deny "test-msg"; '
    'printf "OK:%s:%s\\n" "$TOOL_NAME" "$COMMAND"'
)


def _run_harness(stdin_text: str, env: dict | None = None) -> subprocess.CompletedProcess:
    harness = _HARNESS_TEMPLATE.format(lib=_LIB_SH)
    return subprocess.run(
        ["bash", "-c", harness],
        input=stdin_text,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_valid_bash_payload_returns_ok() -> None:
    """Valid Bash PreToolUse payload → OK with TOOL_NAME=Bash and COMMAND set."""
    result = _run_harness('{"tool_name":"Bash","tool_input":{"command":"ls -la"}}')
    assert result.returncode == 0
    assert result.stdout.startswith("OK:Bash:ls -la"), repr(result.stdout)


def test_empty_stdin_denied() -> None:
    """Empty stdin → DENY (CISO S1.a: empty INPUT path)."""
    result = _run_harness("")
    assert result.returncode == 0
    assert result.stdout.startswith("DENY:"), repr(result.stdout)


def test_whitespace_only_stdin_denied() -> None:
    """Whitespace-only stdin → DENY via jq parse failure (path a).

    Whitespace-only input is non-empty after `$(cat)` (trailing-newline stripping
    doesn't affect leading whitespace), so the empty-INPUT check (path b) does not
    fire. jq rejects the whitespace-only string as invalid JSON and exits non-zero.
    """
    result = _run_harness("   \n\t  ")
    assert result.returncode == 0
    assert result.stdout.startswith("DENY:"), repr(result.stdout)


def test_empty_json_object_denied() -> None:
    """'{}' → DENY (empty TOOL_NAME, CISO S1.a: no .tool_name)."""
    result = _run_harness("{}")
    assert result.returncode == 0
    assert result.stdout.startswith("DENY:"), repr(result.stdout)


def test_empty_tool_name_denied() -> None:
    """Empty .tool_name → DENY."""
    result = _run_harness('{"tool_name":""}')
    assert result.returncode == 0
    assert result.stdout.startswith("DENY:"), repr(result.stdout)


def test_non_object_tool_input_denied_via_jq_exit() -> None:
    """Non-object .tool_input → DENY via jq_exit != 0 (path a).

    The helper uses a single jq string-interpolation call that extracts both
    .tool_name and .tool_input.command. When .tool_input is a string, jq raises
    'Cannot index string with string "command"' and returns non-zero.
    The deny fires from the jq_exit path (a), NOT from the empty-TOOL_NAME
    path (c) — .tool_name would extract cleanly as "Bash" if jq succeeded.
    """
    result = _run_harness('{"tool_name":"Bash","tool_input":"a string"}')
    assert result.returncode == 0
    assert result.stdout.startswith("DENY:"), repr(result.stdout)


def test_non_bash_tool_with_file_path_returns_ok_empty_command() -> None:
    """Edit payload (no .tool_input.command) → OK with empty COMMAND.

    Legitimate non-Bash tools have no 'command' field; the helper allows them
    through with COMMAND set to empty string.
    """
    result = _run_harness('{"tool_name":"Edit","tool_input":{"file_path":"x"}}')
    assert result.returncode == 0
    # COMMAND is empty for non-Bash tools — OK:<tool>:<cmd> where <cmd> is ""
    assert result.stdout.startswith("OK:Edit:"), repr(result.stdout)


def test_hung_jq_denied_within_timeout(tmp_path: Path) -> None:
    """Hung jq (sleeping >5s) → DENY via timeout exit=124, within 6s.

    Verifies _lib_jq's 5s backstop against a jq binary that never terminates.
    Only runs when timeout(1) is available in PATH.
    """
    import shutil

    timeout_path = shutil.which("timeout")
    if not timeout_path:
        pytest.skip("timeout(1) not available — BSD/macOS without coreutils")

    bash_path = shutil.which("bash")
    if not bash_path:
        pytest.skip("bash not found in PATH")

    # Create a fake jq that sleeps 10 seconds, plus stubs for required tools.
    fake_jq = tmp_path / "jq"
    fake_jq.write_text("#!/bin/bash\nsleep 10\n")
    fake_jq.chmod(0o755)

    # Symlink real timeout and bash so the harness can find them.
    (tmp_path / "timeout").symlink_to(timeout_path)
    (tmp_path / "bash").symlink_to(bash_path)
    # Also symlink standard commands needed by the harness.
    for cmd in ["head", "tail", "cat", "cut", "printf"]:
        cmd_path = shutil.which(cmd)
        if cmd_path:
            (tmp_path / cmd).symlink_to(cmd_path)

    env = {"PATH": str(tmp_path), "HOME": str(tmp_path)}
    start = time.monotonic()
    result = _run_harness('{"tool_name":"Bash","tool_input":{"command":"ls"}}', env=env)
    elapsed = time.monotonic() - start

    assert result.returncode == 0
    assert result.stdout.startswith("DENY:"), repr(result.stdout)
    assert elapsed < 6, f"hung-jq test took {elapsed:.1f}s — timeout did not fire within 6s"


def test_timeout_absent_fallback_valid_payload_returns_ok(tmp_path: Path) -> None:
    """Without timeout(1), valid payload still returns OK via bare jq."""
    import shutil

    # Build a PATH that excludes `timeout` but keeps jq and bash.
    jq_path = shutil.which("jq")
    if not jq_path:
        pytest.skip("jq not found in PATH")

    bash_path = shutil.which("bash")
    if not bash_path:
        pytest.skip("bash not found in PATH")

    # Symlink jq and bash into tmp_path but intentionally omit timeout.
    (tmp_path / "jq").symlink_to(jq_path)
    (tmp_path / "bash").symlink_to(bash_path)
    for cmd in ["head", "tail", "cat", "cut", "printf"]:
        cmd_path = shutil.which(cmd)
        if cmd_path:
            (tmp_path / cmd).symlink_to(cmd_path)

    env = {"PATH": str(tmp_path), "HOME": str(tmp_path)}
    result = _run_harness('{"tool_name":"Bash","tool_input":{"command":"ls"}}', env=env)
    assert result.returncode == 0
    assert result.stdout.startswith("OK:Bash:ls"), repr(result.stdout)


def test_missing_emit_deny_loud_fail() -> None:
    """Source _lib.sh WITHOUT defining emit_deny, call helper on empty stdin.

    Verifies that calling _lib_parse_tool_input_or_deny without emit_deny
    produces a loud failure (non-zero exit or error output) rather than
    silent allow. Per the contract comment in _lib.sh: "CALLER MUST define
    emit_deny before sourcing _lib.sh."
    """
    # Harness that sources _lib.sh but intentionally omits emit_deny.
    harness = (
        f". {_LIB_SH}; "
        "_lib_parse_tool_input_or_deny 'test-msg'; "
        'printf "SHOULD_NOT_REACH\\n"'
    )
    result = subprocess.run(
        ["bash", "-c", harness],
        input="",
        capture_output=True,
        text=True,
        check=False,
    )
    # SHOULD_NOT_REACH must never appear on stdout. Hook contract specifies
    # exit 0 always (hooks signal via stdout JSON, not exit code), so we do
    # not assert returncode != 0 — the helper always exits 0 by design.
    # The invariant is that `exit 0` inside the helper terminates the shell
    # before the printf can run; if someone replaces `exit 0` with `return 0`,
    # SHOULD_NOT_REACH would appear and this assertion would catch it.
    assert "SHOULD_NOT_REACH" not in result.stdout, (
        "Calling _lib_parse_tool_input_or_deny without emit_deny must not "
        "silently allow — SHOULD_NOT_REACH must never appear in stdout"
    )


def test_null_literal_input_denied() -> None:
    """JSON null literal → DENY (empty TOOL_NAME after jq, path c).

    `null` is valid JSON and non-empty as a string, so the empty-INPUT check
    (path b) does not fire. jq's `.tool_name // ""` against `null` exits 0 and
    produces an empty string — TOOL_NAME is empty, triggering the empty-TOOL_NAME
    deny (path c). This pins the invariant so a future refactor that removes
    or weakens the empty-TOOL_NAME check cannot silently re-open this path.
    """
    result = _run_harness("null")
    assert result.returncode == 0
    assert result.stdout.startswith("DENY:"), repr(result.stdout)


def test_newline_injection_in_tool_name_denied() -> None:
    """tool_name with embedded newline → DENY (path d: invalid TOOL_NAME shape).

    A JSON string value with an escaped newline (e.g. "Bash\\ninjected") is
    decoded by jq -r into a real newline. The helper uses a unit-separator
    delimiter to split TOOL_NAME from COMMAND, so TOOL_NAME receives
    "Bash\\ninjected" rather than "Bash". The post-extraction newline check
    detects the embedded newline and denies rather than allowing with a
    corrupted TOOL_NAME or COMMAND.
    """
    result = _run_harness('{"tool_name":"Bash\\ninjected","tool_input":{"command":"echo safe"}}')
    assert result.returncode == 0
    assert result.stdout.startswith("DENY:"), repr(result.stdout)
