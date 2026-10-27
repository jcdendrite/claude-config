"""Unit tests for _lib.sh helpers.

Covers _lib_parse_tool_input_or_deny and _lib_jq.

The parse tests drive the helper via a throwaway shell harness that defines
emit_deny before sourcing _lib.sh (the canonical caller pattern), then calls
_lib_parse_tool_input_or_deny and reports either DENY:<msg> or
OK:<tool>:<cmd><0x1e><cwd><0x1e><session_id><0x1e><file_path><0x1e><agent_type><0x1e><agent_id><0x1e><overflow>.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from helpers import (
    DEFAULT_TEST_SESSION_ID,
    HOOKS_DIR,
    assert_cap_engaged,
    bash_input,
    run_hook,
    scaled_shim_sleep,
    write_scaled_timeout_shim,
)

from ._lib_sh_helpers import _LIB_SH

# require-code-review.sh is an unmodified production caller of
# _lib_parse_tool_input_or_deny, used by the delimiter-shift regression test
# below to prove the fixed parser, not just the unit harness, denies.
_REQUIRE_CODE_REVIEW_HOOK = HOOKS_DIR / "require-code-review.sh"


# Shell harness: define emit_deny BEFORE sourcing _lib.sh (canonical pattern),
# call the helper, then print OK:<TOOL_NAME>:<COMMAND> on success, followed by
# the remaining extracted fields and the field-shift overflow variable, each
# separated by 0x1e (a delimiter distinct from the parser's own 0x1f, so
# neither can be mistaken for the other). The "OK:<TOOL_NAME>:<COMMAND>"
# prefix is kept exactly as before so existing startswith() assertions
# elsewhere in this file keep matching unedited.
# {lib} is substituted by the test with the absolute path to _lib.sh.
_HARNESS_TEMPLATE = (
    'emit_deny() {{ printf "DENY:%s\\n" "$1"; exit 0; }}; '
    ". {lib}; "
    '_lib_parse_tool_input_or_deny "test-msg"; '
    'printf "OK:%s:%s\\x1e%s\\x1e%s\\x1e%s\\x1e%s\\x1e%s\\x1e%s\\n" '
    '"$TOOL_NAME" "$COMMAND" "$CWD" "$SESSION_ID" "$FILE_PATH" "$AGENT_TYPE" "$AGENT_ID" "$_lib_parse_overflow"'
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


def _parse_ok_fields(stdout: str) -> dict[str, str]:
    """Split _HARNESS_TEMPLATE's OK line into its extracted fields plus
    the trailing field-shift overflow variable.

    `OK:<TOOL_NAME>:<COMMAND>` keeps the legacy colon-joined prefix intact
    so pre-existing startswith() assertions on that prefix are unaffected;
    everything after it is 0x1e-delimited so COMMAND's own colons or
    embedded newlines can't be mistaken for a field boundary.
    """
    assert stdout.startswith("OK:"), repr(stdout)
    tool_name, _, remainder = stdout[len("OK:") :].partition(":")
    command, cwd, session_id, file_path, agent_type, agent_id, overflow = remainder.split("\x1e")
    return {
        "tool_name": tool_name,
        "command": command,
        "cwd": cwd,
        "session_id": session_id,
        "file_path": file_path,
        "agent_type": agent_type,
        "agent_id": agent_id,
        # printf's own trailing "\n" follows the overflow field; strip
        # exactly that one byte to recover the raw shell value.
        "overflow": overflow[:-1] if overflow.endswith("\n") else overflow,
    }


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


# --- Folded-field extraction and the field-shift detector ---


def test_distinct_field_values_land_without_cross_contamination() -> None:
    """Each payload value lands in its own global with the exact value. This is
    the all-fields analogue of test_valid_bash_payload_returns_ok above.
    Distinct values per field catch a value landing in the wrong global.
    """
    payload = json.dumps(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la", "file_path": "/tmp/f.txt"},
            "cwd": "/repo",
            "session_id": "sess-123",
            "agent_type": "main",
            "agent_id": "agent-abc",
        }
    )
    result = _run_harness(payload)
    assert result.returncode == 0
    fields = _parse_ok_fields(result.stdout)
    assert fields["tool_name"] == "Bash"
    assert fields["command"] == "ls -la"
    assert fields["cwd"] == "/repo"
    assert fields["session_id"] == "sess-123"
    assert fields["file_path"] == "/tmp/f.txt"
    assert fields["agent_type"] == "main"
    assert fields["agent_id"] == "agent-abc"


@pytest.mark.parametrize(
    "agent_type_value,expected",
    [
        (123, "123"),
        ({"nested": "x"}, '{"nested":"x"}'),
        ([1, 2], "[1,2]"),
        (False, ""),
        ("", ""),
    ],
)
def test_non_string_agent_type_stringifies_rather_than_erroring(agent_type_value, expected) -> None:
    """Pins _lib.sh's own comment claim: a non-string .agent_type silently
    stringifies via jq's \\(...) interpolation instead of raising a
    structural-type error, so the folded extraction still exits 0."""
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}, "agent_type": agent_type_value})
    result = _run_harness(payload)
    assert result.returncode == 0
    fields = _parse_ok_fields(result.stdout)
    assert fields["agent_type"] == expected


@pytest.mark.parametrize(
    "agent_id_value,expected",
    [
        (123, "123"),
        ({"nested": "x"}, '{"nested":"x"}'),
        ([1, 2], "[1,2]"),
        (False, ""),
        ("", ""),
    ],
)
def test_non_string_agent_id_stringifies_rather_than_erroring(agent_id_value, expected) -> None:
    """A non-string .agent_id stringifies the same way .agent_type does, so a
    contract-violating value reads as non-empty rather than aborting the parse.
    JSON false is the exception: jq's `//` replaces it, so it reads as absent,
    and an empty string reads empty."""
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}, "agent_id": agent_id_value})
    result = _run_harness(payload)
    assert result.returncode == 0
    fields = _parse_ok_fields(result.stdout)
    assert fields["agent_id"] == expected


def test_agent_type_and_agent_id_are_extracted_independently() -> None:
    """A main session started with `--agent` carries agent_type alone, and a
    subagent with no agent_type carries agent_id alone; neither value may
    land in the other's global."""
    type_only = _parse_ok_fields(
        _run_harness(json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}, "agent_type": "x"})).stdout
    )
    id_only = _parse_ok_fields(
        _run_harness(json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}, "agent_id": "y"})).stdout
    )
    assert (type_only["agent_type"], type_only["agent_id"]) == ("x", "")
    assert (id_only["agent_type"], id_only["agent_id"]) == ("", "y")


def test_multiline_command_round_trips_byte_for_byte() -> None:
    """A default-delimiter `read` truncates COMMAND at its first embedded
    newline; `-d ''` with 0x1f as IFS must preserve one exactly, since a
    Bash command legitimately spans lines (an && chain, or a heredoc)."""
    multiline_command = "echo one &&\necho two"
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": multiline_command}})
    result = _run_harness(payload)
    assert result.returncode == 0
    fields = _parse_ok_fields(result.stdout)
    assert fields["command"] == multiline_command


def test_overflow_variable_holds_only_trailing_newline_for_well_formed_payload() -> None:
    """The field-shift detector's invariant: a well-formed
    payload leaves the overflow variable holding exactly the herestring's
    own trailing newline, and no deny fires. AGENT_TYPE and AGENT_ID carry no
    stray whitespace: the roster test matches AGENT_TYPE exactly, and the
    engineer-row and ledger-state denies in enforce-marker-script-shape.sh
    test AGENT_ID for emptiness."""
    payload = json.dumps(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "ls", "file_path": "/tmp/f"},
            "cwd": "/repo",
            "session_id": "sess-1",
            "agent_type": "main",
            "agent_id": "agent-abc",
        }
    )
    result = _run_harness(payload)
    assert result.returncode == 0
    fields = _parse_ok_fields(result.stdout)
    assert fields["overflow"] == "\n"
    assert not any(ch.isspace() for ch in fields["agent_type"])
    assert not any(ch.isspace() for ch in fields["agent_id"])


def test_overflow_variable_holds_only_trailing_newline_when_final_field_absent() -> None:
    """Trailing jq fields entirely absent from the payload (not merely empty)
    must not be mistaken for an overflow condition. The separators are
    literals in the jq format string, so an absent key's `// ""` default
    still yields an empty field and keeps every delimiter."""
    payload = json.dumps(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "ls", "file_path": "/tmp/f"},
            "cwd": "/repo",
            "session_id": "sess-1",
        }
    )
    result = _run_harness(payload)
    assert result.returncode == 0
    fields = _parse_ok_fields(result.stdout)
    assert fields["agent_type"] == ""
    assert fields["agent_id"] == ""
    assert fields["overflow"] == "\n"


def _extract_parse_fn_slice(lib_sh_text: str) -> str:
    """Slice _lib.sh from `_lib_parse_tool_input_or_deny()`'s definition to
    its closing brace, so the adversarial test's field list is derived from
    the code rather than hand-copied."""
    lines = lib_sh_text.splitlines()
    start = next(i for i, line in enumerate(lines) if re.match(r"^_lib_parse_tool_input_or_deny\(\)", line))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    return "\n".join(lines[start : end + 1])


def _extract_jq_fields(fn_slice: str) -> list[str]:
    """The jq field list, in first-appearance order, from the single-quoted
    argument on the `_lib_jq -r` line."""
    jq_line = next(
        line for line in fn_slice.splitlines() if "_lib_jq -r" in line and not line.lstrip().startswith("#")
    )
    quoted = re.search(r"'(.*)'", jq_line)
    assert quoted is not None, "no single-quoted jq program found on the _lib_jq -r line"
    fields = re.findall(r"\.[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", quoted.group(1))
    seen: list[str] = []
    for field in fields:
        if field not in seen:
            seen.append(field)
    return seen


def _extract_read_names(fn_slice: str) -> list[str]:
    """The `read` variable list, bounded to the substring between `-d ''`
    and the first `<<<`, so the herestring's own tail (`jq_out`, and `x1f`
    from its escape sequence) can't inflate the count. Comment lines are
    skipped: a surrounding comment also mentions `read -r -d ''` without
    the `<<<` that bounds the real code line."""
    read_line = next(
        line
        for line in fn_slice.splitlines()
        if "read -r -d ''" in line and not line.lstrip().startswith("#")
    )
    start = read_line.index("-d ''") + len("-d ''")
    end = read_line.index("<<<", start)
    return re.findall(r"[A-Za-z_][A-Za-z0-9_]*", read_line[start:end])


_PARSE_FN_SLICE = _extract_parse_fn_slice(_LIB_SH.read_text())
_JQ_FIELDS = _extract_jq_fields(_PARSE_FN_SLICE)
_READ_NAMES = _extract_read_names(_PARSE_FN_SLICE)

# jq-field-path → the flat key _parse_ok_fields exposes it under.
_JQ_FIELD_TO_FLAT_KEY = {
    ".tool_name": "tool_name",
    ".tool_input.command": "command",
    ".cwd": "cwd",
    ".session_id": "session_id",
    ".tool_input.file_path": "file_path",
    ".agent_type": "agent_type",
    ".agent_id": "agent_id",
}

_FIELD_SHIFT_DENY_MESSAGE = (
    "a tool-input field contained a Unit Separator (U+001F) byte, which "
    "would shift extracted-field boundaries — refusing rather than acting "
    "on values that may not be the ones the harness sent."
)

# The four infra cause markers, checked case-insensitively by
# transcript-analysis.py's _denial_cause_kind cascade. A field-shift deny
# carrying none of them falls through to that cascade's "behavioral"
# default instead of misfiling a deliberate bypass attempt as infra noise.
_FORBIDDEN_CAUSE_MARKERS = (
    "could not encode its deny reason",
    "could not source _lib.sh",
    "could not parse tool-input json",
    "failing closed",
)


def test_read_variable_count_is_jq_field_count_plus_one() -> None:
    """The structural tie the field-shift detector depends on: one overflow
    variable beyond the jq field count. A field added to one list without
    the other must fail loudly rather than silently lose coverage."""
    assert len(_READ_NAMES) == len(_JQ_FIELDS) + 1


def test_read_variable_order_matches_jq_field_order() -> None:
    """The cardinality check above only proves the two lists are the same
    length. This proves they line up positionally: read_names[i] must be
    the flat-key-uppercased form of jq_fields[i] for every jq field index.
    A future edit that transposes two fields in the jq format string
    without making the mirror transposition in the `read` variable list
    would pass the cardinality check but must fail here."""
    expected_read_names = [_JQ_FIELD_TO_FLAT_KEY[field].upper() for field in _JQ_FIELDS]
    assert _READ_NAMES[: len(_JQ_FIELDS)] == expected_read_names


def test_jq_field_list_extraction_is_non_empty() -> None:
    """Vacuity self-check mirroring test_lib_session_identity.py's own builds_path_re
    precedent: an empty extraction means the regex has drifted from the
    code, not that there are zero fields to guard."""
    assert _JQ_FIELDS, (
        "no jq fields extracted from _lib_parse_tool_input_or_deny — the "
        "regex has drifted from the code and this test is now vacuous"
    )


def _set_dotted_field(payload: dict, jq_field: str, value: str) -> dict:
    """Set VALUE at the dotted path named by a jq field expression such as
    '.tool_input.command', building intermediate objects as needed."""
    keys = jq_field.lstrip(".").split(".")
    node = payload
    for key in keys[:-1]:
        node = node.setdefault(key, {})
    node[keys[-1]] = value
    return payload


def _base_full_payload() -> dict:
    return {
        "tool_name": "Bash",
        "tool_input": {"command": "ls -la", "file_path": "/tmp/f.txt"},
        "cwd": "/repo",
        "session_id": "sess-1",
        "agent_type": "main",
        "agent_id": "agent-abc",
    }


@pytest.mark.parametrize("jq_field", _JQ_FIELDS)
def test_unit_separator_injected_into_any_field_denies_as_field_shift(jq_field: str) -> None:
    """A 0x1f inside any one extracted field must deny with the
    field-shift message rather than silently shifting every later field.
    The field-shift detector is a field-count property, not a per-field
    guard.

    .tool_input.file_path gets its own weight here: it is agent-authored
    text, not harness-controlled, so it is the field an attacker can most
    directly shape.
    """
    payload = _set_dotted_field(_base_full_payload(), jq_field, "va\x1flue")
    result = _run_harness(json.dumps(payload))
    assert result.returncode == 0
    assert result.stdout.startswith("DENY:"), repr(result.stdout)
    reason = result.stdout[len("DENY:") :].rstrip("\n")
    assert reason == _FIELD_SHIFT_DENY_MESSAGE
    lowered = reason.lower()
    for marker in _FORBIDDEN_CAUSE_MARKERS:
        assert marker not in lowered, (jq_field, marker, reason)


# .tool_name already denies on an embedded newline under its own
# pre-existing PreToolUse-contract check (unrelated to the field-shift fix
# above), so it is excluded from the "newline is ordinary data" pairing —
# asserting "must not deny" there would contradict that existing contract.
_NEWLINE_SAFE_JQ_FIELDS = [field for field in _JQ_FIELDS if field != ".tool_name"]


@pytest.mark.parametrize("jq_field", _NEWLINE_SAFE_JQ_FIELDS)
def test_newline_in_field_is_preserved_as_ordinary_data(jq_field: str) -> None:
    """A newline is legal data in a path or command and must not deny —
    paired with the 0x1f-injection test above so the field-shift boundary
    (0x1f specifically, not any control byte) is pinned from both sides."""
    payload = _set_dotted_field(_base_full_payload(), jq_field, "va\nlue")
    result = _run_harness(json.dumps(payload))
    assert result.returncode == 0
    assert result.stdout.startswith("OK:"), repr(result.stdout)
    fields = _parse_ok_fields(result.stdout)
    assert fields[_JQ_FIELD_TO_FLAT_KEY[jq_field]] == "va\nlue"


def test_command_containing_unit_separator_denies_via_require_code_review(
    isolated_home: Path, git_repo: Path
) -> None:
    """Continuity coverage that require-code-review.sh — an unmodified
    production caller of _lib_parse_tool_input_or_deny — still delegates to
    the fixed parser. A literal 0x1f in .tool_input.command must deny, not
    field-shift .cwd and let the review gate's own exit-0 branch fire on an
    empty REPO_ROOT. The invariant itself is proven at the unit layer by the
    adversarial-payload test above. This is end-to-end continuity coverage
    that the production caller reaches the same code path."""
    result = run_hook(
        _REQUIRE_CODE_REVIEW_HOOK,
        bash_input("git commit -m 'x\x1fy'", session_id=DEFAULT_TEST_SESSION_ID),
        cwd=git_repo,
    )
    assert result == "deny"


@pytest.mark.timing
def test_hung_jq_denied_within_timeout(tmp_path: Path) -> None:
    """Hung jq (sleeping >5s) → DENY via timeout exit=124, within 6s.

    Verifies _lib_jq's 5s backstop against a jq binary that never terminates.
    Only runs when timeout(1) is available in PATH.
    """
    import shutil

    timeout_path = shutil.which("timeout") or shutil.which("gtimeout")
    if not timeout_path:
        pytest.skip("neither timeout(1) nor gtimeout(1) available — BSD/macOS without coreutils")

    bash_path = shutil.which("bash")
    if not bash_path:
        pytest.skip("bash not found in PATH")

    # Create a fake jq that sleeps 10 seconds, plus stubs for required tools.
    fake_jq = tmp_path / "jq"
    fake_jq.write_text(f"#!/bin/bash\nsleep {scaled_shim_sleep(10)}\n")
    fake_jq.chmod(0o755)

    # Symlink real timeout and bash so the harness can find them.
    (tmp_path / "timeout").symlink_to(timeout_path)
    (tmp_path / "bash").symlink_to(bash_path)
    # Also symlink standard commands needed by the harness. dirname is
    # required too: _lib.sh's own sourcing of _config.sh resolves its path
    # via `$(dirname "${BASH_SOURCE[0]}")`, and a failed source aborts
    # _lib.sh's own sourcing entirely (see _lib.sh's header comment on that
    # source line). sleep must be symlinked too: the fake jq's body shells
    # out to it, and a missing sleep would return instantly instead of
    # stalling.
    for cmd in ["head", "tail", "cat", "cut", "printf", "dirname", "sleep"]:
        cmd_path = shutil.which(cmd)
        if cmd_path:
            (tmp_path / cmd).symlink_to(cmd_path)

    # write_scaled_timeout_shim replaces the timeout symlink above, since
    # Path.write_text follows a symlink rather than replacing it.
    write_scaled_timeout_shim(tmp_path)
    env = {"PATH": str(tmp_path), "HOME": str(tmp_path)}
    with assert_cap_engaged(tmp_path, production_cap=5):
        result = _run_harness('{"tool_name":"Bash","tool_input":{"command":"ls"}}', env=env)

    assert result.returncode == 0
    assert result.stdout.startswith("DENY:"), repr(result.stdout)


# Deliberately builds a PATH with no timeout(1): installing any fake timeout here removes the absent-binary
# condition this test is named for, and the OK assertion below would still pass.
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
    # dirname is required too: _lib.sh's own sourcing of _config.sh
    # resolves its path via `$(dirname "${BASH_SOURCE[0]}")`, and a failed
    # source now aborts _lib.sh's own sourcing entirely (see _lib.sh's
    # header comment on that source line).
    for cmd in ["head", "tail", "cat", "cut", "printf", "dirname"]:
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
    # An undefined emit_deny causes bash to write "command not found" to stderr.
    # Verifies loudness: the failure is observable (even without a deny JSON),
    # so a hook author who forgets to define emit_deny gets an immediate signal.
    assert result.stderr, (
        "Calling _lib_parse_tool_input_or_deny without emit_deny must produce "
        "a bash 'command not found' error on stderr — per the CALLER MUST define "
        "emit_deny contract in _lib.sh"
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
    decoded by jq -r into a real newline. The 0x1f delimiter split correctly
    extracts TOOL_NAME = "Bash\\ninjected" (the full multi-line string) and
    COMMAND = "echo safe". The deny fires from the post-extraction
    `case "$TOOL_NAME" in *$'\\n'*)` check (path d), not from the split itself.
    """
    result = _run_harness('{"tool_name":"Bash\\ninjected","tool_input":{"command":"echo safe"}}')
    assert result.returncode == 0
    assert result.stdout.startswith("DENY:"), repr(result.stdout)
