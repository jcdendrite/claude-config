"""Unit tests for _lib.sh helpers.

Covers _lib_parse_tool_input_or_deny and _lib_jq.

The parse tests drive the helper via a throwaway shell harness that defines
emit_deny before sourcing _lib.sh (the canonical caller pattern), then calls
_lib_parse_tool_input_or_deny and reports either DENY:<msg> or
OK:<tool>:<cmd><0x1e><cwd><0x1e><session_id><0x1e><file_path><0x1e><agent_type><0x1e><agent_id><0x1e><overflow>.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from helpers import (
    DEFAULT_TEST_SESSION_ID,
    HOOKS_DIR,
    _make_git_exiting_with_status,
    _run_git,
    _sourced_value,
    assert_cap_engaged,
    bare_remote_with_default_branch,
    bash_input,
    build_conflicted_cherry_pick,
    build_conflicted_merge,
    build_conflicted_rebase,
    build_conflicted_revert,
    build_octopus_merge_conflict,
    build_path_without,
    build_rebase_merges_replay_conflict,
    init_git_repo_with_commit,
    push_conflicting_edit_to_origin,
    resolve_conflicted_rebase,
    reviewer_round_state_key,
    run_hook,
    scaled_shim_sleep,
    staged_diff_hash,
    staged_diff_hash_at_base,
    v2_registry_entry,
    write_plugin_registry,
    write_scaled_timeout_shim,
)

from ._lib_sh_helpers import _LIB_SH, _SKILL_MANAGEMENT_PLUGIN_LIB, _run_lib_call
from .test_config_lib import (
    _locale_that_widens_ascii_ranges,
)

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














# --- _lib_length_ratchet_exceeded -----------------------------------------
#
# Extracted out of _lib_staged_length_gate's loop body in _lib.sh so the
# growth-comparison boundary (NEW > LIMIT && NEW > OLD) is reachable without
# a real git repo. _lib_staged_length_gate calls this same predicate for
# both its line-count check and its byte-count check, so the boundary
# matrix below stands in for the pure-triple cases pruned out of
# test_check_claude_md_length.py and test_check_skill_length.py (see the
# hook length-limit tests' end-to-end matrices for the second-dimension
# cases -- path pattern, command parsing, message text, fail-closed
# posture -- that stay end-to-end).


def _length_ratchet_exceeded(new: int, old: int, limit: int | str) -> bool:
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'. {_LIB_SH}; _lib_length_ratchet_exceeded "$@"',
            "bash",
            str(new),
            str(old),
            str(limit),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


class TestLengthRatchetExceeded:
    def test_new_at_exactly_limit_not_exceeded(self) -> None:
        """new == limit is not "over" it -- migrated from
        test_claude_md_at_exactly_200_allows / test_skill_at_exactly_200_allows."""
        assert not _length_ratchet_exceeded(200, 190, 200)

    def test_new_over_limit_and_over_old_exceeded(self) -> None:
        """Crossing the limit for the first time -- migrated from
        test_claude_md_growing_to_201_denies / test_skill_growing_to_201_denies."""
        assert _length_ratchet_exceeded(201, 190, 200)

    def test_new_over_limit_growing_further_while_already_over_exceeded(self) -> None:
        """Already over the limit and growing further still denies --
        migrated from test_already_over_limit_growing_denies."""
        assert _length_ratchet_exceeded(215, 210, 200)

    def test_new_over_limit_but_shrinking_from_old_not_exceeded(self) -> None:
        """Reducing an already-over-limit file is allowed -- migrated from
        test_already_over_limit_reducing_allows."""
        assert not _length_ratchet_exceeded(205, 210, 200)

    def test_new_over_limit_same_size_as_old_not_exceeded(self) -> None:
        """Different content, same count, still over limit: not growing, so
        allowed -- migrated from test_already_over_limit_same_size_allows."""
        assert not _length_ratchet_exceeded(210, 210, 200)

    def test_new_under_limit_growing_not_exceeded(self) -> None:
        """Growing but never crossing the limit -- migrated from
        test_byte_cap_under_limit_growing_allows."""
        assert not _length_ratchet_exceeded(190, 180, 200)

    def test_new_zero_not_exceeded(self) -> None:
        """NEW=0 (a capped-timeout read or a staged deletion) can never be
        "over" a positive limit, regardless of OLD -- boundary case not
        pinned by name in either hook's own test file, since there NEW=0
        arises only as a side effect of git plumbing (deletion, timeout, or
        a missing HEAD) rather than as a directly-asserted value."""
        assert not _length_ratchet_exceeded(0, 300, 200)

    def test_empty_limit_not_exceeded(self) -> None:
        """Non-integer/empty LIMIT makes the underlying `[ -gt ]` test exit 2
        ("integer expression expected") rather than 0 or 1 -- characterizes
        the current fail-open behavior documented on the function's header:
        every caller's `if ...; then deny; fi` treats that exit 2 the same
        as "not exceeded", i.e. allow."""
        assert not _length_ratchet_exceeded(250, 300, "")


# --- _lib_command_concludes_commit / _lib_command_concludes_marker_gated_commit --
#
# The rebase carve-out's two named tri-state predicates: both delegate to
# one private shape-set matcher, so the narrow predicate excludes exactly
# one case (git rebase --continue) from the broad one.
# Pure string processors, no repo needed -- same fast, no-repo,
# fixed-string shape TestCommandInvokesGitSubcmd already uses in test_lib_tool_argv.py.


def _command_concludes_commit(command: str, env: dict | None = None) -> int:
    result = subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_command_concludes_commit "$1"', "bash", command],
        capture_output=True,
        text=True,
        check=False,
        env=env if env is not None else dict(os.environ),
    )
    return result.returncode


def _command_concludes_marker_gated_commit(command: str, env: dict | None = None) -> int:
    result = subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_command_concludes_marker_gated_commit "$1"', "bash", command],
        capture_output=True,
        text=True,
        check=False,
        env=env if env is not None else dict(os.environ),
    )
    return result.returncode


class TestCommandConcludesCommit:
    @pytest.mark.parametrize(
        "command",
        [
            "git commit",
            "git -c core.editor=true commit",
            "GIT_EDITOR=true git merge --continue",
            "git merge --continue",
            "git rebase --continue",
            "git cherry-pick --continue",
            "git revert --continue",
            # git's own option parser accepts any unambiguous prefix of a
            # long option (gitcli(7)) -- these must match the same way.
            "git merge --cont",
            "git rebase --cont",
            "git cherry-pick --cont",
            "git revert --cont",
        ],
    )
    def test_broad_predicate_true_for_concluding_shapes(self, command: str) -> None:
        assert _command_concludes_commit(command) == 0

    @pytest.mark.parametrize(
        "command",
        [
            "git merge origin/main",
            "git rebase --abort",
            "git rebase --skip",
            "git commit-tree abc123",
            "git status",
            # A clean cherry-pick or revert creates its commit inside the
            # initiating command with no separate `git commit` call, so no
            # gate keyed on this predicate ever sees one -- pinned here as
            # suite fact rather than only as prose.
            "git cherry-pick abc123",
            "git revert abc123",
            # A real git merge option, not a prefix of --continue.
            "git merge --commit",
            # Longer than --continue, not a proper prefix of it.
            "git merge --continued",
            "git merge --continue-foo",
        ],
    )
    def test_broad_predicate_false_for_non_concluding_shapes(self, command: str) -> None:
        assert _command_concludes_commit(command) == 1

    @pytest.mark.parametrize(
        "command",
        [
            "git commit",
            "git -c core.editor=true commit",
            "GIT_EDITOR=true git merge --continue",
            "git merge --continue",
            "git cherry-pick --continue",
            "git revert --continue",
            "git merge origin/main",
            "git rebase --abort",
            "git rebase --skip",
            "git commit-tree abc123",
            "git status",
        ],
    )
    def test_narrow_predicate_agrees_with_broad_except_rebase_continue(
        self, command: str
    ) -> None:
        assert _command_concludes_marker_gated_commit(command) == _command_concludes_commit(command)

    @pytest.mark.parametrize(
        "command",
        [
            "git --attr-source HEAD commit -m x",
            "git --attr-source=HEAD commit -m x",
        ],
    )
    def test_both_predicates_recognize_commit_behind_attr_source_global_flag(
        self, command: str
    ) -> None:
        """`--attr-source <tree-ish>` takes a separate-word value, so the
        subcommand walk must skip `HEAD` and find `commit` behind it."""
        assert _command_concludes_commit(command) == 0
        assert _command_concludes_marker_gated_commit(command) == 0

    def test_narrow_predicate_excludes_rebase_continue_while_broad_includes_it(self) -> None:
        """The one input where the two predicates must diverge -- a
        fixture asserting only the narrow predicate's false here would not
        catch a regression that accidentally narrowed both."""
        assert _command_concludes_commit("git rebase --continue") == 0
        assert _command_concludes_marker_gated_commit("git rebase --continue") == 1

    def test_narrow_predicate_excludes_abbreviated_rebase_continue_too(self) -> None:
        """The rebase carve-out must exclude every abbreviated spelling of
        `--continue`, not just the exact one."""
        assert _command_concludes_commit("git rebase --cont") == 0
        assert _command_concludes_marker_gated_commit("git rebase --cont") == 1

    def test_wrong_arity_returns_could_not_determine(self) -> None:
        result = subprocess.run(
            ["bash", "-c", f'. {_LIB_SH}; _lib_command_concludes_commit'],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 2

    def test_sed_absent_returns_could_not_determine(self, tmp_path: Path) -> None:
        farm_dir = tmp_path / "path-without-sed"
        farm_dir.mkdir()
        restricted_path = build_path_without("sed", farm_dir)
        env = {"PATH": restricted_path, "HOME": str(tmp_path)}
        assert _command_concludes_commit("git commit -m x", env=env) == 2
        assert _command_concludes_marker_gated_commit("git commit -m x", env=env) == 2

    def test_tr_absent_returns_could_not_determine(self, tmp_path: Path) -> None:
        farm_dir = tmp_path / "path-without-tr"
        farm_dir.mkdir()
        restricted_path = build_path_without("tr", farm_dir)
        env = {"PATH": restricted_path, "HOME": str(tmp_path)}
        assert _command_concludes_commit("git commit -m x", env=env) == 2
        assert _command_concludes_marker_gated_commit("git commit -m x", env=env) == 2

    @pytest.mark.parametrize(
        ("command", "expected_status_without_shim"),
        [("git commit -m x", 0), ("", 1), ("ls", 1)],
        ids=["concluding-commit", "empty-command", "non-git-command"],
    )
    def test_failed_fragment_read_returns_could_not_determine(
        self, command: str, expected_status_without_shim: int
    ) -> None:
        """A here-string redirect that fails (e.g. an unwritable TMPDIR) skips
        the fragment loop; the matcher must report undetermined, not no-match.
        The `read` shim stands in for that failure without touching the
        filesystem and prints a marker so a shell syntax error (which also
        exits 2) cannot pass for the sentinel.
        Without the shim, the sentinel must not over-deny."""
        shim_marker = "read-shim-ran"

        def run(prelude: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [
                    "bash",
                    "-c",
                    f'. {_LIB_SH}; {prelude or ":"}; _lib_command_concludes_marker_gated_commit "$1"',
                    "bash",
                    command,
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        shimmed = run(f"read() {{ echo {shim_marker}; return 1; }}")
        assert shimmed.returncode == 2
        assert shim_marker in shimmed.stdout
        assert shimmed.stderr == ""
        assert run("").returncode == expected_status_without_shim

    def test_continue_flag_outside_matched_verb_does_not_conclude_commit(self) -> None:
        """A `--continue` word elsewhere in COMMAND, outside the matched
        verb's own arguments, must not read as concluding a commit."""
        assert _command_concludes_commit("git rebase origin/main && echo --continue") == 1

    # The cases below cover the remaining `--continue` abbreviation
    # alternatives in _lib_fragment_concludes_commit_shape's case statement,
    # plus case-sensitivity and the empty command.

    @pytest.mark.parametrize(
        "abbreviation",
        ["--c", "--co", "--con", "--conti", "--contin", "--continu"],
    )
    def test_broad_predicate_true_for_remaining_continue_abbreviations(
        self, abbreviation: str
    ) -> None:
        assert _command_concludes_commit(f"git merge {abbreviation}") == 0

    def test_broad_predicate_case_sensitive_on_continue_flag(self) -> None:
        """`--Continue` is not one of the case statement's alternatives --
        the match is case-sensitive, and a differently-cased spelling must
        not be silently accepted."""
        assert _command_concludes_commit("git merge --Continue") == 1

    def test_broad_predicate_false_for_empty_command(self) -> None:
        assert _command_concludes_commit("") == 1

    # _lib_split_fragments leaves a bare `&` unsplit (GH-1063), so the shared
    # matcher tries each `&`-separated piece of a fragment as its own command.

    @pytest.mark.parametrize(
        "command",
        [
            "git add . & git commit -m x",
            "git status & git merge --continue",
        ],
    )
    def test_both_predicates_true_for_concluding_shape_after_single_ampersand(
        self, command: str
    ) -> None:
        assert _command_concludes_commit(command) == 0
        assert _command_concludes_marker_gated_commit(command) == 0

    def test_rebase_continue_after_single_ampersand_keeps_the_rebase_carve_out(self) -> None:
        command = "git status & git rebase --continue"
        assert _command_concludes_commit(command) == 0
        assert _command_concludes_marker_gated_commit(command) == 1

    def test_ampersand_inside_a_quoted_global_flag_value_still_reaches_commit(self) -> None:
        assert _command_concludes_commit('git -c "user.name=a&b" commit -m x') == 0
        assert _command_concludes_marker_gated_commit('git -c "user.name=a&b" commit -m x') == 0

    def test_fd_redirection_ampersand_does_not_conclude_a_commit(self) -> None:
        assert _command_concludes_commit("git log 2>&1") == 1
        assert _command_concludes_marker_gated_commit("git log 2>&1") == 1

    @pytest.mark.parametrize(
        "command",
        [
            "sleep 1 & (git commit -m x)",
            "(git add) & (git commit -m x)",
            "git status &(git commit -m x)",
            "git status & ( git commit -m x )",
        ],
    )
    def test_both_predicates_true_for_parenthesized_commit_after_single_ampersand(
        self, command: str
    ) -> None:
        """Each `&`-separated piece gets the same leading-`(` / trailing-`)`
        strip a top-level fragment gets."""
        assert _command_concludes_commit(command) == 0
        assert _command_concludes_marker_gated_commit(command) == 0

    @pytest.mark.parametrize("initial_noglob", [False, True], ids=["glob-on", "noglob-on"])
    @pytest.mark.parametrize(
        ("command", "expected_status"),
        [("git status & git commit -m x", 0), ("git status & git log", 1)],
        ids=["concluding-commit", "no-match"],
    )
    def test_ampersand_pass_restores_the_callers_ifs_and_noglob_state(
        self, initial_noglob: bool, command: str, expected_status: int
    ) -> None:
        """Pass 2 word-splits on newlines with globbing off. It must hand
        both settings back as it found them, on a match and on a no-match
        input alike, or every later word-split in the sourcing hook changes."""
        noglob_setup = "set -f; " if initial_noglob else ""
        harness = (
            f". {_LIB_SH}; {noglob_setup}IFS=$' \\t\\n'; "
            '_lib_command_concludes_commit "$1"; status=$?; '
            'case $- in *f*) glob_state=noglob;; *) glob_state=glob;; esac; '
            '[ "$IFS" = $\' \\t\\n\' ] && ifs_restored=true || ifs_restored=false; '
            'printf "%s|%s|%s" "$status" "$glob_state" "$ifs_restored"'
        )
        result = subprocess.run(
            ["bash", "-c", harness, "bash", command], capture_output=True, text=True, check=False
        )
        expected_glob_state = "noglob" if initial_noglob else "glob"
        assert result.stdout == f"{expected_status}|{expected_glob_state}|true"

    @pytest.mark.parametrize("nounset", [False, True], ids=["nounset-off", "nounset-on"])
    def test_ampersand_pass_leaves_an_unset_ifs_unset(self, nounset: bool) -> None:
        """An unset IFS word-splits like the default; restoring it as an empty
        string instead would disable word-splitting for every later loop in
        the sourcing hook."""
        nounset_setup = "set -u; " if nounset else ""
        harness = (
            f". {_LIB_SH}; unset IFS; {nounset_setup}"
            '_lib_command_concludes_commit "$1"; status=$?; '
            '[ -z "${IFS+set}" ] && ifs_state=unset || ifs_state=set; '
            'printf "%s|%s" "$status" "$ifs_state"'
        )
        result = subprocess.run(
            ["bash", "-c", harness, "bash", "git status & git commit -m x"],
            capture_output=True, text=True, check=False,
        )
        assert result.stdout == "0|unset"

    @pytest.mark.parametrize(
        "command",
        ['bash -c "git commit -m x"', 'eval "git commit -m x"'],
    )
    def test_quoted_wrapper_text_naming_a_commit_is_recognized(self, command: str) -> None:
        """Quote-stripping leaves `git commit` as bare words inside a
        `bash -c` or `eval` string, so the wrapper does not hide it."""
        assert _command_concludes_commit(command) == 0

    @pytest.mark.parametrize(
        "command",
        [
            "c=commit; git $c -m x",
            "git ci -m x",
            "bash ./do-commit.sh",
            "git \\\ncommit -m x",
            'git -c "user.name=A B" commit -m x',
            'git -C "dir with space" commit -m x',
            'git -C "$(pwd)" commit -m x',
            'git -C "$(git rev-parse --show-toplevel)" commit -m x',
            'git -C $(pwd) commit -m x',
            'git --git-dir="a b" commit -m x',
        ],
        ids=[
            "variable-word",
            "git-alias",
            "script-file",
            "backslash-newline-continuation",
            "quoted-space-in-global-flag-value",
            "quoted-space-in-dash-C-value",
            "command-substitution-in-dash-C-value",
            "rev-parse-toplevel-substitution-in-dash-C-value",
            "unquoted-command-substitution-in-dash-C-value",
            "quoted-space-in-attached-git-dir-value",
        ],
    )
    def test_known_bypass_commit_text_assembled_outside_the_command_string_is_not_recognized(
        self, command: str
    ) -> None:
        """A commit whose words never appear together in the command text
        (a variable, an alias, a script file, a line continuation, a
        whitespace or fragment-operator value of a global flag that precedes
        the subcommand) is a documented residual of matching command text;
        pinned so a later fix flips it deliberately."""
        assert _command_concludes_commit(command) == 1

    # The matcher is a quote-blind word walk over quote-stripped text, so a
    # command that only names a commit command as an argument, or quotes an
    # `&` inside another command's argument, is read as concluding one. That
    # over-match is the accepted fail-toward-deny posture; each case below pins
    # it so a later narrowing flips these deliberately.

    @pytest.mark.parametrize(
        "command",
        [
            "echo git commit",
            'grep "git commit" docs/x.md',
            "man git commit",
            'git log --grep "fix & git commit hook"',
        ],
        ids=["echo-argument", "grep-quoted-argument", "man-argument", "quoted-ampersand-in-argument"],
    )
    def test_accepted_over_match_command_naming_a_commit_reaches_both_predicates(
        self, command: str
    ) -> None:
        assert _command_concludes_commit(command) == 0
        assert _command_concludes_marker_gated_commit(command) == 0


class TestSplitFragmentsPipefailContract:
    """_lib_split_fragments' first sed stage failing leaves the second stage
    reading empty input and succeeding, so the pipeline reports the failure
    only under `set -o pipefail`; the call-site contract in its header
    requires a sourcing caller to set it."""

    @pytest.mark.parametrize(
        ("pipefail_setting", "expected_status"),
        [("set -o pipefail", 1), ("set +o pipefail", 0)],
        ids=["pipefail-on-reports-failure", "pipefail-off-hides-failure"],
    )
    def test_first_stage_only_failure_is_reported_only_under_pipefail(
        self, pipefail_setting: str, expected_status: int
    ) -> None:
        harness = (
            f". {_LIB_SH}; {pipefail_setting}; "
            # Fails only the first stage, keyed on its script starting `s/;/`.
            "sed() { case \"$2\" in 's/;/'*) return 1;; esac; command sed \"$@\"; }; "
            'fragments=$(_lib_split_fragments "git add . ; git status"); '
            'printf "%s|%s" "$?" "$fragments"'
        )
        result = subprocess.run(["bash", "-c", harness], capture_output=True, text=True, check=False)
        assert result.stdout == f"{expected_status}|"


def _fragment_concludes_commit_via_extraction(fragment: str, env: dict | None = None) -> int:
    """Mirrors what deny-invisible-commit-content.sh's arm 1 and arm 2 each
    do: extract the fragment's own subcommand, then feed FRAGMENT and that
    SUBCMD to _lib_fragment_concludes_commit -- rather than a hand-picked
    SUBCMD, so a regression in the extraction step itself would also show
    up here."""
    result = subprocess.run(
        [
            "bash", "-c",
            f'. {_LIB_SH}; subcmd=$(_lib_extract_git_subcmd "$1"); _lib_fragment_concludes_commit "$1" "$subcmd"',
            "bash", fragment,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env if env is not None else dict(os.environ),
    )
    return result.returncode


class TestFragmentConcludesCommit:
    """Differential coverage for _lib_fragment_concludes_commit_shape, which
    deny-invisible-commit-content.sh's arm 1 (raw/stripped-text SUBCMD
    extraction) and arm 2 (masked-text SUBCMD extraction) share. Asserts the
    fragment-level answer, via its public _lib_fragment_concludes_commit
    wrapper, agrees with the command-level predicate's answer for the
    equivalent single-fragment command."""

    @pytest.mark.parametrize(
        "fragment",
        [
            "git commit",
            "git -c core.editor=true commit",
            "git merge --continue",
            "git rebase --continue",
            "git cherry-pick --continue",
            "git revert --continue",
            "git merge --c",
            "git merge --continu",
        ],
    )
    def test_true_matches_command_level_predicate(self, fragment: str) -> None:
        assert _fragment_concludes_commit_via_extraction(fragment) == 0
        assert _command_concludes_commit(fragment) == 0

    @pytest.mark.parametrize(
        "fragment",
        [
            "git status",
            "git rebase --abort",
            "git rebase --skip",
            "git merge origin/main",
            "git cherry-pick abc123",
        ],
    )
    def test_false_matches_command_level_predicate(self, fragment: str) -> None:
        assert _fragment_concludes_commit_via_extraction(fragment) == 1
        assert _command_concludes_commit(fragment) == 1


def _fragment_concludes_commit_via_arm(fragment: str, transform: str, env: dict | None = None) -> int:
    """Runs one of deny-invisible-commit-content.sh's two real arm
    pipelines: TRANSFORM='strip' applies _lib_strip_shell_quotes (arm 1's
    own transform) and TRANSFORM='mask' applies _lib_mask_shell_quotes
    (arm 2's own transform) to FRAGMENT before extracting SUBCMD and
    feeding both to _lib_fragment_concludes_commit -- so a quoted fixture
    actually exercises the transform each arm runs, not a bypass of both."""
    transform_fn = "_lib_strip_shell_quotes" if transform == "strip" else "_lib_mask_shell_quotes"
    result = subprocess.run(
        [
            "bash", "-c",
            f'. {_LIB_SH}; transformed=$({transform_fn} "$1"); '
            'subcmd=$(_lib_extract_git_subcmd "$transformed"); '
            '_lib_fragment_concludes_commit "$transformed" "$subcmd"',
            "bash", fragment,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env if env is not None else dict(os.environ),
    )
    return result.returncode


class TestFragmentConcludesCommitQuotedDifferential:
    """Fragment-level answers agree with the whole-command predicate on
    quoted input. These fixtures carry a quoted `git` word and a quoted,
    abbreviated `--continue` form for a non-merge/rebase verb, so arm 1's
    quote-stripped extraction and arm 2's quote-masked extraction each run
    for real and must agree with each other and with the command-level
    predicate's answer for the equivalent whole command."""

    @pytest.mark.parametrize(
        "fragment,expected",
        [
            ('"git" cherry-pick "--c"', 0),
            ('"git" revert "--c"', 0),
            ('"git" cherry-pick abc123', 1),
        ],
    )
    def test_stripped_and_masked_arms_agree_with_command_level_predicate(
        self, fragment: str, expected: int
    ) -> None:
        stripped_result = _fragment_concludes_commit_via_arm(fragment, "strip")
        masked_result = _fragment_concludes_commit_via_arm(fragment, "mask")
        assert stripped_result == masked_result == _command_concludes_commit(fragment) == expected


# --- _lib_git_inprogress_state / _lib_gate_diff_base / _lib_staged_diff_hash --
#
# Detection precedence, trust anchors, and reference-tree computation for the
# four in-progress git states (merge/rebase/cherry-pick/revert). See
# git-state-safety/SKILL.md's Rule of thumb for the detection recipe and
# docs/hooks.md's "Which tree a marker describes" section for how a gate
# consumes the resulting base.


def _git_inprogress_state(repo: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_git_inprogress_state "$1"', "bash", str(repo)],
        capture_output=True,
        text=True,
        check=False,
        env=env if env is not None else dict(os.environ),
    )


def _gate_diff_base(
    repo: Path, env: dict | None = None, timeout: float | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_gate_diff_base "$1"', "bash", str(repo)],
        capture_output=True,
        text=True,
        check=False,
        env=env if env is not None else dict(os.environ),
        timeout=timeout,
    )


def _staged_diff_hash(
    repo: Path, base: str, *pathspecs: str, env: dict | None = None, timeout: float | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "bash", "-c",
            f'. {_LIB_SH}; _lib_staged_diff_hash "$1" "$2" "${{@:3}}"',
            "bash", str(repo), base, *pathspecs,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env if env is not None else dict(os.environ),
        timeout=timeout,
    )


def _forge_state_marker(gitdir: Path, state: str, oid: str) -> None:
    """Write the on-disk shape _lib_git_inprogress_state/_lib_gate_diff_base
    read for `state`, without running the real git operation -- for
    precedence and forged-ref tests that only need the marker shape, not a
    genuine conflict."""
    if state == "rebase":
        (gitdir / "rebase-merge").mkdir(exist_ok=True)
        (gitdir / "rebase-merge" / "head-name").write_text("refs/heads/feature\n")
        (gitdir / "REBASE_HEAD").write_text(oid + "\n")
    elif state == "merge":
        (gitdir / "MERGE_HEAD").write_text(oid + "\n")
    elif state == "cherry-pick":
        (gitdir / "CHERRY_PICK_HEAD").write_text(oid + "\n")
    elif state == "revert":
        (gitdir / "REVERT_HEAD").write_text(oid + "\n")
    else:
        raise ValueError(state)


def _assert_valid_tree_oid(repo: Path, oid: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{oid}^{{tree}}"],
        capture_output=True,
    )
    assert result.returncode == 0, f"{oid!r} is not a valid tree oid"


class TestGitInprogressStateDetection:
    def test_detects_merge(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_conflicted_merge(repo)
        result = _git_inprogress_state(repo)
        assert result.returncode == 0
        assert result.stdout == "merge"

    def test_detects_cherry_pick(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_conflicted_cherry_pick(repo)
        result = _git_inprogress_state(repo)
        assert result.returncode == 0
        assert result.stdout == "cherry-pick"

    def test_detects_revert(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_conflicted_revert(repo)
        result = _git_inprogress_state(repo)
        assert result.returncode == 0
        assert result.stdout == "revert"

    def test_detects_rebase_plain(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_conflicted_rebase(repo)
        result = _git_inprogress_state(repo)
        assert result.returncode == 0
        assert result.stdout == "rebase"

    def test_rebase_precedence_over_stale_cherry_pick_head(self, tmp_path: Path) -> None:
        """The precedence table requires rebase to win a tie against a
        stale CHERRY_PICK_HEAD alongside rebase-merge/. Older git
        left a real CHERRY_PICK_HEAD during an interactive rebase's pick
        step (each "pick" reuses cherry-pick's own machinery); confirmed
        empirically that the git version running this suite no longer does,
        so the CHERRY_PICK_HEAD half of this fixture is forged directly
        here, alongside a genuine conflicted interactive rebase for the
        rebase half."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        feature_tip = build_conflicted_rebase(repo, interactive=True)
        gitdir = repo / ".git"
        (gitdir / "CHERRY_PICK_HEAD").write_text(feature_tip + "\n")
        result = _git_inprogress_state(repo)
        assert result.returncode == 0
        assert result.stdout == "rebase"

    def test_no_state_in_ordinary_repo(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        result = _git_inprogress_state(repo)
        assert result.returncode == 1
        assert result.stdout == ""

    def test_undetermined_when_git_missing(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        farm_dir = tmp_path / "path-without-git"
        farm_dir.mkdir()
        restricted_path = build_path_without("git", farm_dir)
        env = {"PATH": restricted_path, "HOME": str(tmp_path)}
        result = _git_inprogress_state(repo, env=env)
        assert result.returncode == 2
        assert result.stdout == ""

    @pytest.mark.parametrize(
        "higher, lower",
        [
            ("rebase", "merge"),
            ("rebase", "revert"),
            ("merge", "cherry-pick"),
            ("merge", "revert"),
            ("cherry-pick", "revert"),
        ],
    )
    def test_precedence_pair_via_forged_files(
        self, tmp_path: Path, higher: str, lower: str
    ) -> None:
        """The remaining five precedence pairs: forge two of the four marker
        shapes directly (no real conflicting operation) and assert the
        higher-precedence state wins, per the table's strict order
        rebase > merge > cherry-pick > revert. Without this, a future
        reordering of the detection if/elif chain regresses silently on any
        pair but rebase-vs-cherry-pick, which the previous test covers with
        a real fixture."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        gitdir = repo / ".git"
        head = _run_git(repo, "rev-parse", "HEAD").strip()
        _forge_state_marker(gitdir, higher, head)
        _forge_state_marker(gitdir, lower, head)
        result = _git_inprogress_state(repo)
        assert result.returncode == 0
        assert result.stdout == higher

    def test_wrong_arity_returns_could_not_determine(self) -> None:
        result = subprocess.run(
            ["bash", "-c", f'. {_LIB_SH}; _lib_git_inprogress_state'],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 2
        assert result.stdout == ""


def test_build_conflicted_rebase_pre_resolution_checkpoint_has_all_three_stages(
    tmp_path: Path,
) -> None:
    """build_conflicted_rebase's docstring claims genuine stage 1/2/3
    entries at the pre-resolution checkpoint -- distinct from a 2-way
    add/add conflict, which would carry only stages 2 and 3 with no common
    ancestor. `git ls-files --unmerged` reports one line per populated
    stage for the conflicted path."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo, branch="main")
    build_conflicted_rebase(repo)
    unmerged = _run_git(repo, "ls-files", "--unmerged")
    stages = {line.split()[2] for line in unmerged.splitlines() if line}
    assert stages == {"1", "2", "3"}


def test_resolve_conflicted_rebase_advances_to_staged_checkpoint(tmp_path: Path) -> None:
    """The rebase fixture's two checkpoints: pre-add (unresolved, genuine
    stage 1/2/3 entries) and post-resolution (staged, ready for
    `git rebase --continue`)."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo, branch="main")
    build_conflicted_rebase(repo)
    resolve_conflicted_rebase(repo)
    status = _run_git(repo, "status", "--porcelain=v1")
    assert "UU" not in status and "AA" not in status
    staged_names = _run_git(repo, "diff", "--cached", "--name-only")
    assert "f" in staged_names
    continue_result = subprocess.run(
        ["git", "rebase", "--continue"],
        cwd=repo,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_EDITOR": "true"},
    )
    assert continue_result.returncode == 0, continue_result.stderr


def test_git_diff_cached_against_unresolved_conflict_git_primitive_fact(
    tmp_path: Path,
) -> None:
    """What `git diff --cached` does against an index still carrying
    unmerged (stage 1/2/3) entries, pinned as a git-primitive fact
    independent of any hook. deny-pii-in-commits.sh and
    deny-private-project-refs.sh both route `git rebase --continue`, and
    their hook-level counterparts are
    test_continue_at_unresolved_conflict_checkpoint_is_a_clean_passthrough in
    test_deny_pii_in_commits.py and test_deny_private_project_refs.py."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo, branch="main")
    build_conflicted_rebase(repo)  # pre-`git add` checkpoint: unresolved
    result = subprocess.run(
        ["git", "diff", "--cached"], cwd=repo, capture_output=True, text=True
    )
    assert result.returncode == 0
    # git's diff machinery has no single index blob to diff against HEAD for
    # an unmerged path, so `--cached` reports only an "Unmerged path" marker
    # line for it, never the file's actual conflicting content -- a scanner
    # relying on this recipe alone would not see a conflicted file's real
    # content pre-resolution. Substring match, not an exact-stdout pin: the
    # leading marker glyph is git's own free-text diff-output wording, not a
    # machine-stable contract.
    assert "Unmerged path f" in result.stdout


class TestGateDiffBaseNoState:
    def test_no_state_exits_1_with_empty_base(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        (repo / "f.txt").write_text("y\n")
        _run_git(repo, "add", "f.txt")
        result = _gate_diff_base(repo)
        assert result.returncode == 1
        assert result.stdout == ""

    def test_no_state_staged_diff_hash_matches_production_recipe(
        self, tmp_path: Path
    ) -> None:
        """Proves no marker on disk invalidates: with no base override,
        _lib_staged_diff_hash must produce the exact byte-identical digest
        today's production `git diff --cached | sha256sum` recipe does."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        (repo / "f.txt").write_text("y\n")
        _run_git(repo, "add", "f.txt")
        result = _staged_diff_hash(repo, "")
        assert result.returncode == 0
        assert result.stdout == staged_diff_hash(repo)

    def test_wrong_arity_returns_could_not_determine(self) -> None:
        result = subprocess.run(
            ["bash", "-c", f'. {_LIB_SH}; _lib_gate_diff_base'],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 2
        assert result.stdout == ""


class TestGateDiffBaseTrustedAnchor:
    """Verification: for each of the four states, with a trusted anchor
    present, _lib_gate_diff_base exits 0 and prints a tree OID."""

    def test_merge_trusted_via_origin_default_branch(self, tmp_path: Path) -> None:
        bare, clone = bare_remote_with_default_branch(tmp_path)
        (clone / "f").write_text("ours-edit\n")
        _run_git(clone, "add", "f")
        _run_git(clone, "commit", "-qm", "ours edits f")
        push_conflicting_edit_to_origin(tmp_path, bare, "f", "origin-edit\n")
        _run_git(clone, "fetch", "-q", "origin")
        result = subprocess.run(
            ["git", "merge", "-q", "origin/main"], cwd=clone, capture_output=True, text=True
        )
        assert result.returncode != 0, result.stdout + result.stderr
        assert (clone / ".git" / "MERGE_HEAD").exists()

        base_result = _gate_diff_base(clone)
        assert base_result.returncode == 0
        assert base_result.stdout != ""
        _assert_valid_tree_oid(clone, base_result.stdout)

    def test_cherry_pick_trusted_via_origin_when_source_already_upstream(
        self, tmp_path: Path
    ) -> None:
        """A cherry-pick passes only when its source is already upstream
        (backporting a landed hotfix)."""
        bare, clone = bare_remote_with_default_branch(tmp_path)
        push_conflicting_edit_to_origin(tmp_path, bare, "f", "source-edit\n")
        _run_git(clone, "fetch", "-q", "origin")
        source_oid = _run_git(clone, "rev-parse", "origin/main").strip()
        (clone / "f").write_text("local-edit\n")
        _run_git(clone, "add", "f")
        _run_git(clone, "commit", "-qm", "local edits f")
        result = subprocess.run(
            ["git", "cherry-pick", source_oid], cwd=clone, capture_output=True, text=True
        )
        assert result.returncode != 0, result.stdout + result.stderr
        assert (clone / ".git" / "CHERRY_PICK_HEAD").exists()

        base_result = _gate_diff_base(clone)
        assert base_result.returncode == 0
        _assert_valid_tree_oid(clone, base_result.stdout)

    def test_revert_trusted_via_head(self, tmp_path: Path) -> None:
        """REVERT_HEAD is an ancestor of HEAD by construction, so a
        genuine revert always trusts via the HEAD anchor."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_conflicted_revert(repo)
        base_result = _gate_diff_base(repo)
        assert base_result.returncode == 0
        _assert_valid_tree_oid(repo, base_result.stdout)

    def test_rebase_trusted_via_origin_when_replayed_commit_already_pushed(
        self, tmp_path: Path
    ) -> None:
        """Atypical in practice -- a rebase fails both anchors in the
        ordinary case, since REBASE_HEAD is the pre-rebase commit being
        replayed and mid-rebase HEAD is the new base plus already-replayed
        commits -- but this exercises the anchor mechanism uniformly across
        all four states: REBASE_HEAD reaches
        origin/<default> when the replayed commit was already pushed there
        before the rebase started."""
        bare, clone = bare_remote_with_default_branch(tmp_path)
        _run_git(clone, "checkout", "-qb", "upstream")
        (clone / "f").write_text("upstream-edit\n")
        _run_git(clone, "add", "f")
        _run_git(clone, "commit", "-qm", "upstream edits f")
        _run_git(clone, "checkout", "-q", "main")
        (clone / "f").write_text("feature-edit\n")
        _run_git(clone, "add", "f")
        _run_git(clone, "commit", "-qm", "feature edits f")
        _run_git(clone, "push", "-q", "origin", "main")
        result = subprocess.run(
            ["git", "rebase", "upstream"], cwd=clone, capture_output=True, text=True
        )
        assert result.returncode != 0, result.stdout + result.stderr
        gitdir = clone / ".git"
        assert (gitdir / "rebase-apply").exists() or (gitdir / "rebase-merge").exists()

        base_result = _gate_diff_base(clone)
        assert base_result.returncode == 0
        _assert_valid_tree_oid(clone, base_result.stdout)


class TestGateDiffBaseSha256ObjectFormat:
    def test_sha256_repo_mid_revert_returns_valid_64_hex_tree_oid(
        self, tmp_path: Path
    ) -> None:
        """state_oid's shape validation accepts both a 40-hex SHA-1 and a
        64-hex SHA-2 oid (_lib.sh's `_lib_gate_diff_base`), but every other
        fixture in this file builds an ordinary SHA-1 repo -- this is the
        only one that exercises the 64-hex branch end-to-end."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "init", "-q", "--object-format=sha256", "-b", "main", str(repo)],
            check=True,
        )
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        build_conflicted_revert(repo)

        result = _gate_diff_base(repo)

        assert result.returncode == 0
        assert re.fullmatch(r"[0-9a-f]{64}", result.stdout), (
            f"expected a 64-hex SHA-256 tree oid, got {result.stdout!r}"
        )
        _assert_valid_tree_oid(repo, result.stdout)


class TestGateDiffBaseAnchorNamespaceShadow:
    """A local branch or tag literally named `origin/<default>` outranks the
    remote-tracking ref under git's short-name resolution, so the anchor
    check must use the fully-qualified refs/remotes/ path."""

    @pytest.mark.parametrize("shadow_kind", ["branch", "tag"])
    @pytest.mark.parametrize("has_remote_tracking_ref", [True, False])
    @pytest.mark.parametrize("state", ["merge", "cherry-pick"])
    def test_local_ref_named_like_remote_tracking_ref_is_not_the_anchor(
        self, tmp_path: Path, shadow_kind: str, has_remote_tracking_ref: bool, state: str
    ) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        _run_git(repo, "checkout", "-qb", "side")
        (repo / "f.txt").write_text("unreviewed\n")
        _run_git(repo, "add", "f.txt")
        _run_git(repo, "commit", "-qm", "unreviewed commit")
        unreviewed_oid = _run_git(repo, "rev-parse", "HEAD").strip()
        _run_git(repo, "checkout", "-q", "main")
        if has_remote_tracking_ref:
            _run_git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        if shadow_kind == "branch":
            _run_git(repo, "branch", "origin/main", unreviewed_oid)
        else:
            _run_git(repo, "tag", "origin/main", unreviewed_oid)
        _forge_state_marker(repo / ".git", state, unreviewed_oid)

        # Precondition: the short name resolves to the shadow, so the test
        # fails against an anchor spelled `origin/<default>`.
        shadowed_oid = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "origin/main"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert shadowed_oid == unreviewed_oid

        result = _gate_diff_base(repo)
        assert result.returncode == 1
        assert result.stdout == ""


class TestGateDiffBaseUntrustedAnchor:
    def test_reaches_neither_anchor_falls_back_to_empty_base(self, tmp_path: Path) -> None:
        """A cherry-pick whose source was never pushed anywhere over-gates
        -- the right answer, since that content genuinely has not been
        reviewed on this branch."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_conflicted_cherry_pick(repo)
        result = _gate_diff_base(repo)
        assert result.returncode == 1
        assert result.stdout == ""

    @pytest.mark.parametrize("state", ["rebase", "merge", "cherry-pick", "revert"])
    def test_forged_ref_pointing_at_unreachable_commit_falls_back(
        self, tmp_path: Path, state: str
    ) -> None:
        """A state ref pointed at a commit reachable from neither anchor
        -- an orphan-history commit, sharing no ancestry with the
        checked-out branch at all -- must not be trusted."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        _run_git(repo, "checkout", "-q", "--orphan", "orphan")
        (repo / "orphan.txt").write_text("z\n")
        _run_git(repo, "add", "orphan.txt")
        _run_git(repo, "commit", "-qm", "orphan commit")
        orphan_oid = _run_git(repo, "rev-parse", "HEAD").strip()
        _run_git(repo, "checkout", "-q", "main")
        _forge_state_marker(repo / ".git", state, orphan_oid)
        result = _gate_diff_base(repo)
        assert result.returncode == 1
        assert result.stdout == ""

    @pytest.mark.parametrize("state", ["rebase", "merge", "cherry-pick", "revert"])
    def test_reachable_only_from_fabricated_remote_tracking_ref_still_falls_back(
        self, tmp_path: Path, state: str
    ) -> None:
        """A declined third anchor, pinned as a negative assertion: a
        commit reachable only from a hand-created
        refs/remotes/origin/<fabricated-name> must not be trusted, since
        _lib_resolve_default_branch's candidate probe is deliberately
        narrow (exactly main/master/develop, never a refs/remotes/* pattern)
        and never resolves to this name."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        _run_git(repo, "checkout", "-qb", "side")
        (repo / "f.txt").write_text("side\n")
        _run_git(repo, "add", "f.txt")
        _run_git(repo, "commit", "-qm", "side commit")
        side_oid = _run_git(repo, "rev-parse", "HEAD").strip()
        _run_git(repo, "checkout", "-q", "main")
        _run_git(repo, "update-ref", "refs/remotes/origin/totally-not-the-default", side_oid)
        _forge_state_marker(repo / ".git", state, side_oid)
        result = _gate_diff_base(repo)
        assert result.returncode == 1
        assert result.stdout == ""

    @pytest.mark.parametrize("state", ["rebase", "merge", "cherry-pick", "revert"])
    def test_resolvable_non_hex_state_ref_falls_back(
        self, tmp_path: Path, state: str
    ) -> None:
        """A state ref file is plain, unauthenticated content anyone with
        filesystem access could write directly -- not proof of a real git
        operation. `HEAD` is syntactically valid and trivially its own
        ancestor, so without state_oid's own shape validation it would
        sail past the anchor check and reach merge-tree, producing a real
        tree OID (rc=0) instead of falling back. A `--flag`-shaped payload
        would not discriminate here: git's own CLI parser already rejects
        an unrecognized option in merge-base/merge-tree, independent of
        state_oid's shape guard, so it can't prove the guard is
        load-bearing."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        _forge_state_marker(repo / ".git", state, "HEAD")
        result = _gate_diff_base(repo)
        assert result.returncode == 1
        assert result.stdout == ""


class TestGateDiffBaseTopologyFallback:
    def test_octopus_merge_falls_back_to_empty_base(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_octopus_merge_conflict(repo)
        result = _gate_diff_base(repo)
        assert result.returncode == 1
        assert result.stdout == ""

    def test_octopus_merge_head_falls_back_even_when_first_line_alone_is_trusted(
        self, tmp_path: Path
    ) -> None:
        """The stronger adversarial sub-case, distinct from the
        both-untrusted-lines fixture above: line 1 of MERGE_HEAD is by
        itself a genuine ancestor of HEAD, with only line 2 unreachable.
        state_oid's own shape validation rejects the multi-line value
        outright before any anchor check runs; even without that guard,
        `git merge-base --is-ancestor` fails to parse a multi-line revision
        regardless of which line would individually pass. Either way this
        must still fall back to the empty base -- not treat line 1's own
        trust as good enough for the whole value."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        trusted_oid = _run_git(repo, "rev-parse", "HEAD").strip()
        _run_git(repo, "checkout", "-qb", "untrusted-line")
        (repo / "f.txt").write_text("untrusted\n")
        _run_git(repo, "add", "f.txt")
        _run_git(repo, "commit", "-qm", "untrusted commit")
        untrusted_oid = _run_git(repo, "rev-parse", "HEAD").strip()
        _run_git(repo, "checkout", "-q", "main")
        (repo / ".git" / "MERGE_HEAD").write_text(f"{trusted_oid}\n{untrusted_oid}\n")
        result = _gate_diff_base(repo)
        assert result.returncode == 1
        assert result.stdout == ""

    def test_rebase_merges_replay_of_merge_commit_falls_back_to_empty_base(
        self, tmp_path: Path
    ) -> None:
        """The --rebase-merges hazard: REBASE_HEAD names a merge commit, so
        REBASE_HEAD^ would silently resolve to parent 1 rather than
        erroring. Closed because the replayed merge commit reaches neither
        anchor, not by a second mechanism."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_rebase_merges_replay_conflict(repo)
        result = _gate_diff_base(repo)
        assert result.returncode == 1
        assert result.stdout == ""


class TestMergeBaseIsAncestorPrimitiveContract:
    """Pins git's own documented `merge-base --is-ancestor` exit contract
    as a suite fact, since _lib_gate_diff_base's trust check depends on
    treating every non-zero exit identically as "not trusted"."""

    def test_ancestor_returns_zero(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        first = _run_git(repo, "rev-parse", "HEAD").strip()
        (repo / "f.txt").write_text("y\n")
        _run_git(repo, "add", "f.txt")
        _run_git(repo, "commit", "-qm", "second")
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", first, "HEAD"], cwd=repo, capture_output=True
        )
        assert result.returncode == 0

    def test_non_ancestor_returns_nonzero(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        _run_git(repo, "checkout", "-qb", "side")
        (repo / "f.txt").write_text("side\n")
        _run_git(repo, "add", "f.txt")
        _run_git(repo, "commit", "-qm", "side commit")
        side = _run_git(repo, "rev-parse", "HEAD").strip()
        _run_git(repo, "checkout", "-q", "main")
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", side, "HEAD"], cwd=repo, capture_output=True
        )
        assert result.returncode != 0

    def test_unresolvable_oid_returns_nonzero(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", "0" * 40, "HEAD"], cwd=repo, capture_output=True
        )
        assert result.returncode != 0


def test_git_diff_cached_accepts_bare_tree_oid(tmp_path: Path) -> None:
    """_lib_gate_diff_base's stdout is a bare tree OID, and callers diff
    staged content against it directly -- pins that `git diff --cached`
    accepts one."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo, branch="main")
    tree_oid = _run_git(repo, "rev-parse", "HEAD^{tree}").strip()
    (repo / "f.txt").write_text("y\n")
    _run_git(repo, "add", "f.txt")
    result = subprocess.run(
        ["git", "diff", "--cached", tree_oid], cwd=repo, capture_output=True, text=True
    )
    assert result.returncode == 0


def test_merge_tree_merge_base_flag_changes_computed_tree(tmp_path: Path) -> None:
    """--merge-base='s effect on the computed tree is observed here, not
    merely its acceptance as a flag. root -> middle (changes "a") -> tip
    (changes "b" only, so
    tip still carries middle's "a" change). Auto-detecting the merge-base
    of (root, tip) yields root (an ancestor), so merging root and tip
    trivially reproduces tip's own tree. Forcing --merge-base=middle
    instead treats "a" as also having changed on root's side (a revert of
    middle's change), producing a materially different, still
    non-conflicting tree."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init", "-q", "-b", "main")
    _run_git(repo, "config", "user.email", "t@t.com")
    _run_git(repo, "config", "user.name", "t")
    (repo / "a").write_text("1\n")
    (repo / "b").write_text("1\n")
    _run_git(repo, "add", "a", "b")
    _run_git(repo, "commit", "-qm", "root")
    root = _run_git(repo, "rev-parse", "HEAD").strip()
    (repo / "a").write_text("2\n")
    _run_git(repo, "add", "a")
    _run_git(repo, "commit", "-qm", "middle changes a")
    middle = _run_git(repo, "rev-parse", "HEAD").strip()
    (repo / "b").write_text("2\n")
    _run_git(repo, "add", "b")
    _run_git(repo, "commit", "-qm", "tip changes b")
    tip = _run_git(repo, "rev-parse", "HEAD").strip()

    auto = _run_git(repo, "merge-tree", "--write-tree", root, tip).strip().splitlines()[0]
    forced = _run_git(
        repo, "merge-tree", "--write-tree", f"--merge-base={middle}", root, tip
    ).strip().splitlines()[0]
    assert auto != forced
    assert auto == _run_git(repo, "rev-parse", f"{tip}^{{tree}}").strip()


def _make_blocking_merge_tree_git(bin_dir: Path) -> Path:
    """Shim at bin_dir/git: for `merge-tree` specifically, writes a partial
    line to stdout then blocks past the 5s cap; every other subcommand
    proxies to the real git. Sleeps 20s, ~4x the 5s cap -- the same bounded
    overshoot convention test_lib_capped_for_enforces_cap_when_timeout_present
    uses (5s sleep against a 1s cap) to keep a cap regression from hanging
    the test."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "git"
    shim.write_text(
        '#!/bin/bash\n'
        'for arg in "$@"; do\n'
        '  if [ "$arg" = "merge-tree" ]; then\n'
        '    printf "partialline"\n'
        '    sleep 20\n'
        '    exit 0\n'
        '  fi\n'
        'done\n'
        'exec "$REAL_GIT" "$@"\n'
    )
    shim.chmod(0o755)
    return shim


def _make_blocking_absolute_git_dir_git(bin_dir: Path) -> Path:
    """Shim at bin_dir/git: for `rev-parse --absolute-git-dir` specifically,
    writes a partial line to stdout then blocks past the 5s cap; every other
    subcommand proxies to the real git. Same shape as
    _make_blocking_merge_tree_git above, targeting _lib_gate_diff_base's
    initial `git rev-parse --absolute-git-dir` call instead of its
    merge-tree call."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "git"
    shim.write_text(
        '#!/bin/bash\n'
        'for arg in "$@"; do\n'
        '  if [ "$arg" = "--absolute-git-dir" ]; then\n'
        '    printf "partialline"\n'
        '    sleep 20\n'
        '    exit 0\n'
        '  fi\n'
        'done\n'
        'exec "$REAL_GIT" "$@"\n'
    )
    shim.chmod(0o755)
    return shim


def _make_blocking_tree_verify_git(bin_dir: Path) -> Path:
    """Shim at bin_dir/git: for a `<tree-oid>^{tree}` revision argument
    specifically, writes a partial line to stdout then blocks past the 5s
    cap; every other subcommand proxies to the real git. Same shape as
    _make_blocking_merge_tree_git above, targeting _lib_gate_diff_base's
    final `git rev-parse --verify --quiet "${tree_oid}^{tree}"` validation
    call instead of its merge-tree call."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "git"
    shim.write_text(
        '#!/bin/bash\n'
        'for arg in "$@"; do\n'
        '  case "$arg" in\n'
        '    *"^{tree}")\n'
        '      printf "partialline"\n'
        '      sleep 20\n'
        '      exit 0\n'
        '      ;;\n'
        '  esac\n'
        'done\n'
        'exec "$REAL_GIT" "$@"\n'
    )
    shim.chmod(0o755)
    return shim


def _make_blocking_diff_git(bin_dir: Path) -> Path:
    """Shim at bin_dir/git: for `diff` specifically, writes a partial line to
    stdout then blocks past the 5s cap; every other subcommand proxies to the
    real git. Same shape as _make_blocking_merge_tree_git above, targeting
    _lib_staged_diff_hash's `git diff --cached` call instead of
    _lib_gate_diff_base's `merge-tree` call."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "git"
    shim.write_text(
        '#!/bin/bash\n'
        'for arg in "$@"; do\n'
        '  if [ "$arg" = "diff" ]; then\n'
        '    printf "partialline"\n'
        '    sleep 20\n'
        '    exit 0\n'
        '  fi\n'
        'done\n'
        'exec "$REAL_GIT" "$@"\n'
    )
    shim.chmod(0o755)
    return shim


def _make_call_logging_git(bin_dir: Path, log_file: Path) -> Path:
    """Shim at bin_dir/git: appends the full argument list to log_file, one
    invocation per line, then proxies to the real git."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "git"
    shim.write_text(
        '#!/bin/bash\n'
        f'printf "%s\\n" "$*" >> "{log_file}"\n'
        'exec "$REAL_GIT" "$@"\n'
    )
    shim.chmod(0o755)
    return shim


class TestGateDiffBaseMergeTreeFailure:
    """A real merge-tree failure falls back to the empty base."""

    @staticmethod
    def _repo_with_a_root_commit_and_a_second_commit(tmp_path: Path) -> tuple[Path, str, str]:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        root_oid = _run_git(repo, "rev-parse", "HEAD").strip()
        (repo / "f.txt").write_text("y\n")
        _run_git(repo, "add", "f.txt")
        _run_git(repo, "commit", "-qm", "second")
        second_oid = _run_git(repo, "rev-parse", "HEAD").strip()
        return repo, root_oid, second_oid

    @pytest.mark.parametrize("state", ["rebase", "cherry-pick", "revert"])
    def test_root_commit_state_oid_falls_back_to_empty_base(
        self, tmp_path: Path, state: str
    ) -> None:
        """REBASE_HEAD^/CHERRY_PICK_HEAD^/REVERT_HEAD^ is invalid when the
        state ref names a root commit (no parent) -- a git-primitive fact,
        so the merge-tree call this design issues fails."""
        repo, root_oid, _ = self._repo_with_a_root_commit_and_a_second_commit(tmp_path)
        _forge_state_marker(repo / ".git", state, root_oid)
        result = _gate_diff_base(repo)
        assert result.returncode == 1
        assert result.stdout == ""

    @pytest.mark.parametrize("state", ["rebase", "cherry-pick", "revert"])
    def test_same_forge_with_a_state_oid_that_has_a_parent_computes_a_base(
        self, tmp_path: Path, state: str
    ) -> None:
        """Control for the root-commit test above: the identical forged
        marker, but naming the second commit, reaches merge-tree and gets
        a tree back. So that test's exit 1 comes from the merge-tree
        failure, not from an earlier return."""
        repo, _, second_oid = self._repo_with_a_root_commit_and_a_second_commit(tmp_path)
        _forge_state_marker(repo / ".git", state, second_oid)
        result = _gate_diff_base(repo)
        assert result.returncode == 0
        _assert_valid_tree_oid(repo, result.stdout)

    def test_merge_of_an_unrelated_history_falls_back_to_empty_base(self, tmp_path: Path) -> None:
        """origin/<default> forged at an orphan commit makes MERGE_HEAD pass
        the anchor check, and `merge-tree` then refuses to merge unrelated
        histories."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        _run_git(repo, "checkout", "-q", "--orphan", "orphan")
        (repo / "orphan.txt").write_text("z\n")
        _run_git(repo, "add", "orphan.txt")
        _run_git(repo, "commit", "-qm", "orphan commit")
        orphan_oid = _run_git(repo, "rev-parse", "HEAD").strip()
        _run_git(repo, "checkout", "-q", "main")
        _run_git(repo, "update-ref", "refs/remotes/origin/main", orphan_oid)
        _forge_state_marker(repo / ".git", "merge", orphan_oid)
        result = _gate_diff_base(repo)
        assert result.returncode == 1
        assert result.stdout == ""

    def test_same_merge_forge_with_a_related_history_computes_a_base(self, tmp_path: Path) -> None:
        """Control for the unrelated-history merge test above: origin/<default>
        and MERGE_HEAD forged at a commit descending from HEAD reach
        merge-tree and get a tree back."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        _run_git(repo, "checkout", "-qb", "side")
        (repo / "side.txt").write_text("s\n")
        _run_git(repo, "add", "side.txt")
        _run_git(repo, "commit", "-qm", "side commit")
        side_oid = _run_git(repo, "rev-parse", "HEAD").strip()
        _run_git(repo, "checkout", "-q", "main")
        _run_git(repo, "update-ref", "refs/remotes/origin/main", side_oid)
        _forge_state_marker(repo / ".git", "merge", side_oid)
        result = _gate_diff_base(repo)
        assert result.returncode == 0
        _assert_valid_tree_oid(repo, result.stdout)


def _timeout_binary_present() -> bool:
    return shutil.which("timeout") is not None or shutil.which("gtimeout") is not None


class TestGateDiffBaseCapFaultInjection:
    @pytest.mark.timing
    @pytest.mark.skipif(
        not _timeout_binary_present(), reason="no timeout/gtimeout on PATH to fire the cap"
    )
    def test_blocking_merge_tree_returns_undetermined_with_empty_stdout(
        self, tmp_path: Path
    ) -> None:
        """Proves the safety property directly -- a partial or candidate
        OID interrupted mid-write must never escape onto stdout, since
        every caller consumes stdout unconditionally regardless of exit
        status. timeout=30 bounds a cap regression to a fast failure
        instead of a 20s hang (the shim's own sleep)."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_conflicted_revert(repo)
        bin_dir = tmp_path / "bin-blocking-merge-tree"
        _make_blocking_merge_tree_git(bin_dir)
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "REAL_GIT": shutil.which("git")}
        result = _gate_diff_base(repo, env=env, timeout=30)
        assert result.returncode == 2
        assert result.stdout == ""

    @pytest.mark.timing
    @pytest.mark.skipif(
        not _timeout_binary_present(), reason="no timeout/gtimeout on PATH to fire the cap"
    )
    def test_blocking_absolute_git_dir_returns_undetermined_with_empty_stdout(
        self, tmp_path: Path
    ) -> None:
        """Same safety property as the merge-tree case above, for
        _lib_gate_diff_base's initial `git rev-parse --absolute-git-dir`
        call -- its own independent `|| return 2` must not let a partial
        gitdir escape onto stdout either."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        bin_dir = tmp_path / "bin-blocking-absolute-git-dir"
        _make_blocking_absolute_git_dir_git(bin_dir)
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "REAL_GIT": shutil.which("git")}
        result = _gate_diff_base(repo, env=env, timeout=30)
        assert result.returncode == 2
        assert result.stdout == ""

    @pytest.mark.timing
    @pytest.mark.skipif(
        not _timeout_binary_present(), reason="no timeout/gtimeout on PATH to fire the cap"
    )
    def test_blocking_tree_verify_returns_undetermined_with_empty_stdout(
        self, tmp_path: Path
    ) -> None:
        """Same safety property as the merge-tree case above, for
        _lib_gate_diff_base's final `git rev-parse --verify --quiet
        "${tree_oid}^{tree}"` validation call -- its own independent
        exit-code-match block must not let the unvalidated tree_oid escape
        onto stdout either."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_conflicted_revert(repo)
        bin_dir = tmp_path / "bin-blocking-tree-verify"
        _make_blocking_tree_verify_git(bin_dir)
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "REAL_GIT": shutil.which("git")}
        result = _gate_diff_base(repo, env=env, timeout=30)
        assert result.returncode == 2
        assert result.stdout == ""


class TestGateDiffBaseCapKillStatusesAreUndetermined:
    """Every status a cap kill can produce -- 124 (GNU SIGTERM kill), 143
    (BusyBox SIGTERM kill), and 137 (SIGKILL after the grace, on both) --
    must read as undetermined (exit 2), not as the "no override" answer
    (exit 1). See _lib_capped_for's header in _lib.sh for the mapping. No
    real cap is engaged: the shim exits with the status directly."""

    @pytest.mark.parametrize("cap_kill_status", [124, 137, 143])
    @pytest.mark.parametrize(
        "arg_pattern",
        ["merge-tree", '*"^{tree}"'],
        ids=["merge-tree", "tree-verify"],
    )
    def test_cap_kill_status_returns_undetermined_with_empty_stdout(
        self, tmp_path: Path, arg_pattern: str, cap_kill_status: int
    ) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_conflicted_revert(repo)
        bin_dir = tmp_path / "bin-exiting-with-status"
        _make_git_exiting_with_status(bin_dir, arg_pattern, cap_kill_status)
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "REAL_GIT": shutil.which("git")}
        result = _gate_diff_base(repo, env=env)
        assert result.returncode == 2
        assert result.stdout == ""


class TestGateDiffBaseMergeTreeSkippedWhenAnchorFails:
    def test_merge_tree_never_invoked_when_state_reaches_no_anchor(
        self, tmp_path: Path
    ) -> None:
        """merge-tree runs only after an ancestry check succeeds, never
        unconditionally and discarded on failure -- otherwise the 5s
        cap and merge-tree's rename-detection cost would be charged on
        every ordinary conflicted rebase, the exact arm this design newly
        gates."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_conflicted_cherry_pick(repo)  # source never pushed: untrusted
        log_file = tmp_path / "git-calls.log"
        bin_dir = tmp_path / "bin-logging"
        _make_call_logging_git(bin_dir, log_file)
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "REAL_GIT": shutil.which("git")}
        result = _gate_diff_base(repo, env=env)
        assert result.returncode == 1
        calls = log_file.read_text() if log_file.exists() else ""
        assert "merge-tree" not in calls


class TestGateDiffBaseSingleCallInvocationCount:
    """One call to _lib_gate_diff_base must not internally invoke state
    detection, merge-tree, or the ancestor checks more than the documented
    number of times -- a per-call invocation-count property. Whether a
    caller resolves the base once per hook invocation and threads it
    through, rather than re-resolving inside a per-file loop, is a property
    of that caller's own call site (e.g. a while-loop body iterating staged
    files), which has no such call site in this codebase yet to test."""

    def test_single_invocation_computes_merge_tree_and_ancestor_check_once(
        self, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_conflicted_revert(repo)  # HEAD-anchor trusted, no origin configured
        log_file = tmp_path / "git-calls.log"
        bin_dir = tmp_path / "bin-logging"
        _make_call_logging_git(bin_dir, log_file)
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "REAL_GIT": shutil.which("git")}
        result = _gate_diff_base(repo, env=env)
        assert result.returncode == 0
        calls = log_file.read_text().splitlines()
        merge_tree_calls = [c for c in calls if "merge-tree" in c]
        ancestor_calls = [c for c in calls if "merge-base" in c and "--is-ancestor" in c]
        assert len(merge_tree_calls) == 1
        # No origin is configured in this fixture, so _lib_resolve_default_branch
        # returns empty and the origin leg's is-ancestor call never runs --
        # only the HEAD leg does.
        assert len(ancestor_calls) == 1


class TestGateDiffBaseNoCapBinary:
    def test_runs_uncapped_and_still_succeeds_without_timeout_or_gtimeout(
        self, tmp_path: Path
    ) -> None:
        """With neither timeout(1) nor gtimeout(1) on PATH, the wrapped git
        commands run to completion uncapped rather than failing or
        returning a cap-driven status 2 -- proving the *absence* of a cap
        actually degrades to running uncapped, not only that the capped
        path works."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_conflicted_revert(repo)
        farm_dir = tmp_path / "path-without-timeout"
        farm_dir.mkdir()
        restricted_path = build_path_without("timeout", farm_dir)
        gtimeout_shim = farm_dir / "gtimeout"
        if gtimeout_shim.exists():
            gtimeout_shim.unlink()
        env = {"PATH": restricted_path, "HOME": str(tmp_path)}
        result = _gate_diff_base(repo, env=env)
        assert result.returncode == 0
        assert result.stdout != ""


class TestGateDiffBaseOriginForgeryDocumentedBehavior:
    def test_explicit_destination_fetch_forges_origin_default_and_is_trusted(
        self, tmp_path: Path
    ) -> None:
        """A documented-behavior assertion, not a self-healing guarantee.
        The payload must be a descendant of origin/<default>'s
        current tip -- an explicit-destination fetch to an existing
        remote-tracking ref is still subject to ordinary non-fast-forward
        rejection without a leading + or --force, and this is specifically
        meant to demonstrate the ordinary, unforced form."""
        bare, clone = bare_remote_with_default_branch(tmp_path)
        _run_git(clone, "checkout", "-qb", "payload-branch")
        (clone / "f").write_text("payload\n")
        _run_git(clone, "add", "f")
        _run_git(clone, "commit", "-qm", "unreviewed payload")
        payload_oid = _run_git(clone, "rev-parse", "HEAD").strip()
        _run_git(clone, "checkout", "-q", "main")

        # Ordinary, unforced explicit-destination fetch from the local repo
        # itself -- no attacker infrastructure needed to demonstrate.
        _run_git(clone, "fetch", "-q", ".", "payload-branch:refs/remotes/origin/main")
        assert _run_git(clone, "rev-parse", "origin/main").strip() == payload_oid

        _forge_state_marker(clone / ".git", "merge", payload_oid)
        result = _gate_diff_base(clone)
        assert result.returncode == 0, (
            "the forged origin/main ref must be trusted exactly as a genuine one would be"
        )
        _assert_valid_tree_oid(clone, result.stdout)


class TestReviewerRoundStateKeyDuringRebase:
    """HEAD is detached for the whole duration of a rebase, so
    _lib_reviewer_round_state_key (keyed on branch name) cannot resolve --
    the round-3 architect-consult gate is disarmed for the operation's
    duration. This is a property of _lib_reviewer_round_state_key's
    existing branch-keying design, unrelated to and unaffected by the
    novel-content base this file's other tests cover."""

    def test_plain_rebase_leaves_head_detached(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_conflicted_rebase(repo)
        symbolic_ref = subprocess.run(
            ["git", "symbolic-ref", "-q", "--short", "HEAD"], cwd=repo, capture_output=True
        )
        assert symbolic_ref.returncode != 0
        assert reviewer_round_state_key(repo) == ""

    def test_interactive_rebase_leaves_head_detached(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_conflicted_rebase(repo, interactive=True)
        symbolic_ref = subprocess.run(
            ["git", "symbolic-ref", "-q", "--short", "HEAD"], cwd=repo, capture_output=True
        )
        assert symbolic_ref.returncode != 0
        assert reviewer_round_state_key(repo) == ""


class TestStagedDiffHash:
    def test_empty_base_matches_production_recipe(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        (repo / "f.txt").write_text("changed\n")
        _run_git(repo, "add", "f.txt")
        result = _staged_diff_hash(repo, "")
        assert result.returncode == 0
        assert result.stdout == staged_diff_hash(repo)

    def test_non_empty_base_matches_independent_oracle(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        tree_oid = _run_git(repo, "rev-parse", "HEAD^{tree}").strip()
        (repo / "f.txt").write_text("changed\n")
        _run_git(repo, "add", "f.txt")
        result = _staged_diff_hash(repo, tree_oid)
        assert result.returncode == 0
        assert result.stdout == staged_diff_hash_at_base(repo, tree_oid)

    def test_pathspec_restricts_diff(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        (repo / "other.txt").write_text("z\n")
        _run_git(repo, "add", "other.txt")
        _run_git(repo, "commit", "-qm", "add other.txt")
        (repo / "f.txt").write_text("changed\n")
        (repo / "other.txt").write_text("changed too\n")
        _run_git(repo, "add", "f.txt", "other.txt")
        result = _staged_diff_hash(repo, "", "f.txt")
        expected_diff = subprocess.run(
            ["git", "diff", "--cached", "--", "f.txt"], cwd=repo, capture_output=True, check=True
        ).stdout
        expected = hashlib.sha256(expected_diff).hexdigest()
        assert result.returncode == 0
        assert result.stdout == expected

    def test_sha256sum_absent_returns_empty_and_fails_closed(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        (repo / "f.txt").write_text("changed\n")
        _run_git(repo, "add", "f.txt")
        farm_dir = tmp_path / "path-without-sha256sum"
        farm_dir.mkdir()
        restricted_path = build_path_without("sha256sum", farm_dir)
        env = {"PATH": restricted_path, "HOME": str(tmp_path)}
        result = _staged_diff_hash(repo, "", env=env)
        assert result.returncode == 1
        assert result.stdout == ""

    @pytest.mark.timing
    @pytest.mark.skipif(
        not _timeout_binary_present(), reason="no timeout/gtimeout on PATH to fire the cap"
    )
    def test_blocking_diff_returns_empty_stdout_not_partial_hash(
        self, tmp_path: Path
    ) -> None:
        """Proves the same safety property TestGateDiffBaseCapFaultInjection
        proves for `merge-tree` -- ${PIPESTATUS[0]} must catch a `git diff`
        killed past the cap so a partial or interrupted diff never gets
        hashed onto stdout. timeout=30 bounds a cap regression to a fast
        failure instead of a 20s hang (the shim's own sleep)."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        (repo / "f.txt").write_text("changed\n")
        _run_git(repo, "add", "f.txt")
        bin_dir = tmp_path / "bin-blocking-diff"
        _make_blocking_diff_git(bin_dir)
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "REAL_GIT": shutil.which("git")}
        result = _staged_diff_hash(repo, "", env=env, timeout=30)
        assert result.returncode == 1
        assert result.stdout == ""


class TestLibAcquireAppendLockCalledTwice:
    """_lib_acquire_append_lock's own docstring documents that calling it
    twice in the same process orphans the first call's lock file, since the
    second call's `trap ... EXIT` replaces rather than stacks on the
    first's. review-ledger.sh and log-reviewer-round.sh each call it at
    most once per process, so this exercises the primitive directly rather
    than through either caller."""

    def test_second_call_orphans_the_first_lock_file(self, tmp_path: Path) -> None:
        first_lock = tmp_path / "first.lock"
        second_lock = tmp_path / "second.lock"
        result = subprocess.run(
            ["bash", "-c",
             f'. "{_LIB_SH}"; _lib_acquire_append_lock "$1"; _lib_acquire_append_lock "$2"',
             "_", str(first_lock), str(second_lock)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert not second_lock.exists(), "the second call's own lock must be released on exit"
        assert first_lock.exists(), (
            "the first call's lock must be left orphaned once the second "
            "call's EXIT trap replaces the first's"
        )


def _run_ledger_sweep_window_days(settings_file: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'. "{_LIB_SH}"; _ledger_sweep_window_days "$1"', "_", str(settings_file)],
        capture_output=True,
        text=True,
        check=False,
    )


class TestLedgerSweepWindowDays:
    """GH-973: _ledger_sweep_window_days derives the ledger's sweep window
    from Claude Code's own cleanupPeriodDays setting, floored at
    _LEDGER_SWEEP_FLOOR_DAYS. review-ledger.sh's own
    TestReviewLedgerSweepWindowFromSettings (test_review_ledger_script.py)
    covers this value's wiring into clear-stale's `find -mtime +N`; the
    arithmetic itself is pinned here instead."""

    _MISSING_OR_NON_NUMERIC_MESSAGE = (
        "_ledger_sweep_window_days: cleanupPeriodDays missing, unreadable, "
        "or non-numeric -- using the 30-day floor"
    )
    _NINE_PLUS_DIGITS_MESSAGE = (
        "_ledger_sweep_window_days: cleanupPeriodDays has 9+ digits, "
        "implausibly large -- using the 30-day floor"
    )

    def test_defaults_to_thirty_when_settings_file_absent(self, tmp_path: Path) -> None:
        result = _run_ledger_sweep_window_days(tmp_path / "nonexistent-settings.json")
        assert result.returncode == 0, result.stderr
        assert result.stdout == "30"
        assert self._MISSING_OR_NON_NUMERIC_MESSAGE in result.stderr

    def test_custom_cleanup_period_days_is_honored(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"cleanupPeriodDays": 60}))
        result = _run_ledger_sweep_window_days(settings_file)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "60"
        assert result.stderr == ""

    def test_value_below_the_floor_is_floored_to_thirty(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"cleanupPeriodDays": 5}))
        result = _run_ledger_sweep_window_days(settings_file)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "30"
        assert (
            "_ledger_sweep_window_days: configured value 5 is below the "
            "30-day floor, using the floor instead"
        ) in result.stderr

    def test_malformed_settings_json_defaults_to_thirty(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text("{not valid json")
        result = _run_ledger_sweep_window_days(settings_file)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "30"
        assert self._MISSING_OR_NON_NUMERIC_MESSAGE in result.stderr

    def test_non_numeric_cleanup_period_days_defaults_to_thirty(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"cleanupPeriodDays": "60"}))
        result = _run_ledger_sweep_window_days(settings_file)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "30"
        assert self._MISSING_OR_NON_NUMERIC_MESSAGE in result.stderr

    def test_negative_cleanup_period_days_defaults_to_thirty(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"cleanupPeriodDays": -5}))
        result = _run_ledger_sweep_window_days(settings_file)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "30"
        assert self._MISSING_OR_NON_NUMERIC_MESSAGE in result.stderr

    @pytest.mark.parametrize("cleanup_period_days", [90.0, 45.5])
    def test_fractional_cleanup_period_days_defaults_to_thirty_not_truncated(
        self, tmp_path: Path, cleanup_period_days: float
    ) -> None:
        """jq's `select(type == "number")` passes a whole-number float like
        90.0 through as the string "90.0", which the digit-only case
        pattern rejects -- this pins that the result is the floor (30),
        never the truncated integer (90)."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"cleanupPeriodDays": cleanup_period_days}))
        result = _run_ledger_sweep_window_days(settings_file)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "30"
        assert self._MISSING_OR_NON_NUMERIC_MESSAGE in result.stderr

    def test_absurdly_large_all_digit_value_floors_to_thirty_without_erroring(
        self, tmp_path: Path
    ) -> None:
        """A 9+-digit all-digit value can exceed bash's signed-integer
        range, making the `-lt` floor comparison itself error and fall
        through unfloored instead of returning _LEDGER_SWEEP_FLOOR_DAYS --
        this pins the digit-count guard added to reject it before that
        comparison runs."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"cleanupPeriodDays": 99999999999999999999}))
        result = _run_ledger_sweep_window_days(settings_file)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "30"
        assert self._NINE_PLUS_DIGITS_MESSAGE in result.stderr

    def test_eight_digit_value_below_the_nine_digit_guard_is_not_floored(
        self, tmp_path: Path
    ) -> None:
        """99999999 (8 digits) sits one digit under the 9-digit guard's
        threshold and is a real, above-floor value -- pins that the guard
        doesn't fire early and truncate a legitimate large-but-in-range
        cleanupPeriodDays."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"cleanupPeriodDays": 99999999}))
        result = _run_ledger_sweep_window_days(settings_file)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "99999999"
        assert result.stderr == ""

    def test_nine_digit_value_at_the_guard_threshold_floors_to_thirty(
        self, tmp_path: Path
    ) -> None:
        """100000000 (9 digits) is the guard's exact threshold -- pins that
        the boundary itself floors, not just values far past it."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"cleanupPeriodDays": 100000000}))
        result = _run_ledger_sweep_window_days(settings_file)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "30"
        assert self._NINE_PLUS_DIGITS_MESSAGE in result.stderr


# --- _lib_conflict_marker_deny_paths -----------------------------------------
#
# Plugin-only helper (no stowed-copy counterpart -- marker.sh, the write
# side, has no conflict-marker scan to share it with), so these source
# _SKILL_MANAGEMENT_PLUGIN_LIB directly rather than _LIB_SH.
# Pure set-difference: exact-line matching semantics only. The scan's
# git-dependent properties (index-vs-worktree read, diff-presentation-config
# immunity, the marker regex's own boundary behavior) stay pinned at the
# subprocess-fixture layer in test_require_skill_review.py's
# TestSkillReviewGateConflictMarkerHardDeny.


def _conflict_marker_deny_paths(
    candidates: str, marker_free: str, env: dict | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "bash", "-c",
            f'. {_SKILL_MANAGEMENT_PLUGIN_LIB}; _lib_conflict_marker_deny_paths "$1" "$2"',
            "bash", candidates, marker_free,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env if env is not None else dict(os.environ),
    )


class TestConflictMarkerDenyPaths:
    def test_exact_line_match_is_excluded(self) -> None:
        result = _conflict_marker_deny_paths("skills/x/SKILL.md", "skills/x/SKILL.md")
        assert result.returncode == 0
        assert result.stdout == ""

    def test_suffix_of_a_marker_free_entry_is_not_treated_as_marker_free(self) -> None:
        """A clean `plugins/p/skills/x/SKILL.md` ends with the conflicted
        `skills/x/SKILL.md` -- exact-line match must not let that suffix
        relationship clear the shorter path."""
        conflicted = "skills/x/SKILL.md"
        clean_sibling = "plugins/p/skills/x/SKILL.md"
        result = _conflict_marker_deny_paths(conflicted, clean_sibling)
        assert result.returncode == 0
        assert result.stdout == conflicted

    def test_prefix_of_a_marker_free_entry_is_not_treated_as_marker_free(self) -> None:
        """The reverse relationship: a clean `skills/x/SKILL.md.bak` starts
        with the conflicted `skills/x/SKILL.md` -- still not a match."""
        conflicted = "skills/x/SKILL.md"
        clean_sibling = "skills/x/SKILL.md.bak"
        result = _conflict_marker_deny_paths(conflicted, clean_sibling)
        assert result.returncode == 0
        assert result.stdout == conflicted

    def test_empty_candidates_produces_no_deny_paths(self) -> None:
        result = _conflict_marker_deny_paths("", "skills/x/SKILL.md")
        assert result.returncode == 0
        assert result.stdout == ""

    def test_empty_marker_free_returns_candidates_unchanged(self) -> None:
        candidates = "skills/a/SKILL.md\nskills/b/SKILL.md"
        result = _conflict_marker_deny_paths(candidates, "")
        assert result.returncode == 0
        assert result.stdout == candidates

    def test_mixed_candidates_clear_only_the_marker_free_entries(self) -> None:
        """Order-preserving partial removal: the marker-free candidate drops
        out, the other two stay in their original order."""
        candidates = "skills/a/SKILL.md\nskills/b/SKILL.md\nskills/c/SKILL.md"
        marker_free = "skills/b/SKILL.md\nskills/z/SKILL.md"
        result = _conflict_marker_deny_paths(candidates, marker_free)
        assert result.returncode == 0
        assert result.stdout == "skills/a/SKILL.md\nskills/c/SKILL.md"

    def test_all_candidates_marker_free_produces_no_deny_paths(self) -> None:
        candidates = "skills/a/SKILL.md\nskills/b/SKILL.md"
        result = _conflict_marker_deny_paths(candidates, candidates)
        assert result.returncode == 0
        assert result.stdout == ""


# --- _lib_trial_merge_verdict -------------------------------------------------

# The two `set` modes the helper's callers run under: the divergence script
# (-e) and the SessionStart hook (no -e).
_TRIAL_MERGE_CALLER_SHELL_MODES = ["set -euo pipefail", "set -uo pipefail"]

_TRIAL_MERGE_TREE_OID_SHA1 = "a" * 40
_TRIAL_MERGE_TREE_OID_SHA256 = "b" * 64


def _trial_merge_verdict(
    repo: Path, upstream_ref: str, shell_mode: str, env: dict | None = None
) -> subprocess.CompletedProcess:
    """Run _lib_trial_merge_verdict inside an `if`, the way both callers do, so
    the helper's own status reaches the test unmasked by `set -e`."""
    harness = (
        f'{shell_mode}; . {_LIB_SH}; '
        'if _lib_trial_merge_verdict "$1"; then exit 0; else exit "$?"; fi'
    )
    return subprocess.run(
        ["bash", "-c", harness, "bash", upstream_ref],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env=env if env is not None else dict(os.environ),
    )


def _commit_files(repo: Path, files: dict[str, str], message: str) -> None:
    for relative_path, content in files.items():
        file_path = repo / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        _run_git(repo, "add", "--", relative_path)
    _run_git(repo, "commit", "-qm", message)


def _repo_with_upstream_and_feature_edits(
    tmp_path: Path,
    base_files: dict[str, str],
    upstream_files: dict[str, str],
    feature_files: dict[str, str],
) -> Path:
    """A repo checked out on `feature`, with a diverged local branch `upstream`
    that both descend from one base commit holding base_files."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo, branch="main")
    _commit_files(repo, base_files, "base")
    _run_git(repo, "checkout", "-qb", "upstream")
    _commit_files(repo, upstream_files, "upstream edits")
    _run_git(repo, "checkout", "-qb", "feature", "main")
    _commit_files(repo, feature_files, "feature edits")
    return repo


def _make_git_printing_on_merge_tree(bin_dir: Path, stdout_text: str, exit_status: int) -> Path:
    """Shim at bin_dir/git: `merge-tree` prints stdout_text and exits
    exit_status; every other subcommand proxies to the real git. Callers must
    set REAL_GIT in the shim's environment."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "git"
    shim.write_text(
        '#!/bin/bash\n'
        'if [ "$1" = "merge-tree" ]; then\n'
        f'  printf "%s\\n" {shlex.quote(stdout_text)}\n'
        f'  exit {exit_status}\n'
        'fi\n'
        'exec "$REAL_GIT" "$@"\n'
    )
    shim.chmod(0o755)
    return shim


def _env_with_shim_first(bin_dir: Path) -> dict:
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "REAL_GIT": shutil.which("git")}


@pytest.mark.parametrize("shell_mode", _TRIAL_MERGE_CALLER_SHELL_MODES)
class TestTrialMergeVerdict:
    """The helper's contract: exit 0 with a CLEAN or CONFLICT line, or exit 1
    with empty stdout and one stderr line when git merge-tree cannot complete."""

    def test_clean_merge_reports_clean(self, tmp_path: Path, shell_mode: str) -> None:
        repo = _repo_with_upstream_and_feature_edits(
            tmp_path, {"shared.txt": "base\n"}, {"upstream.txt": "u\n"}, {"feature.txt": "f\n"}
        )
        result = _trial_merge_verdict(repo, "upstream", shell_mode)
        assert result.returncode == 0
        assert result.stdout == "Trial merge: CLEAN\n"

    def test_one_file_conflict_names_the_file(self, tmp_path: Path, shell_mode: str) -> None:
        repo = _repo_with_upstream_and_feature_edits(
            tmp_path, {"shared.txt": "base\n"}, {"shared.txt": "upstream\n"}, {"shared.txt": "feature\n"}
        )
        result = _trial_merge_verdict(repo, "upstream", shell_mode)
        assert result.returncode == 0
        assert result.stdout == "Trial merge: CONFLICT in: shared.txt\n"

    def test_three_file_conflict_lists_paths_sorted_with_a_space_kept_whole(
        self, tmp_path: Path, shell_mode: str
    ) -> None:
        conflicted_paths = ["z.txt", "dir/b c.txt", "a.txt"]
        repo = _repo_with_upstream_and_feature_edits(
            tmp_path,
            {path: "base\n" for path in conflicted_paths},
            {path: "upstream\n" for path in conflicted_paths},
            {path: "feature\n" for path in conflicted_paths},
        )
        result = _trial_merge_verdict(repo, "upstream", shell_mode)
        assert result.returncode == 0
        assert result.stdout == "Trial merge: CONFLICT in: a.txt, dir/b c.txt, z.txt\n"

    def test_modify_delete_conflict_reports_the_path_when_stage_2_is_absent(
        self, tmp_path: Path, shell_mode: str
    ) -> None:
        """Upstream, the first argument of `git merge-tree --write-tree upstream HEAD`,
        deleting the file leaves stage lines 1 and 3 only, because stage numbers
        follow merge-tree's argument order. The path must still be reported,
        so the filter cannot key on a single stage number."""
        # The upstream.txt decoy only makes upstream's commit non-empty before the
        # delete. It must stay dissimilar to shared.txt, or rename detection
        # would pair the two and the merge would come out clean.
        repo = _repo_with_upstream_and_feature_edits(
            tmp_path, {"shared.txt": "base\n"}, {"upstream.txt": "u\n"}, {"shared.txt": "feature\n"}
        )
        _run_git(repo, "checkout", "-q", "upstream")
        _run_git(repo, "rm", "-q", "--", "shared.txt")
        _run_git(repo, "commit", "-qm", "upstream deletes shared.txt")
        _run_git(repo, "checkout", "-q", "feature")
        result = _trial_merge_verdict(repo, "upstream", shell_mode)
        assert result.returncode == 0
        assert result.stdout == "Trial merge: CONFLICT in: shared.txt\n"

    def test_add_add_conflict_reports_the_path_when_stage_1_is_absent(
        self, tmp_path: Path, shell_mode: str
    ) -> None:
        """A path added on both branches has no merge base, so stage 1 is absent
        and only stage lines 2 and 3 appear. The path must still be reported,
        so the filter cannot key on a single stage number."""
        repo = _repo_with_upstream_and_feature_edits(
            tmp_path, {"shared.txt": "base\n"}, {"added.txt": "upstream\n"}, {"added.txt": "feature\n"}
        )
        result = _trial_merge_verdict(repo, "upstream", shell_mode)
        assert result.returncode == 0
        assert result.stdout == "Trial merge: CONFLICT in: added.txt\n"

    def test_cleanly_merged_path_with_a_forged_stage_line_in_its_name_is_not_reported(
        self, tmp_path: Path, shell_mode: str
    ) -> None:
        """Without --no-messages, the messages section prints a path unquoted, so
        a newline in a cleanly merged path's name would forge a stage line there."""
        forged_stage_line = "100644 " + "a" * 40 + " 1\tFORGED"
        forged_stage_line_path = "notes\n" + forged_stage_line
        # Both branches edit the forged-name path in non-overlapping hunks, so git
        # content-merges it cleanly and prints an "Auto-merging" message for it.
        # a.txt conflicts on purpose, because merge-tree prints the messages section
        # by default only when the merge has conflicts.
        base_lines = [f"line {number}\n" for number in range(1, 6)]
        upstream_lines = ["line 1 upstream\n", *base_lines[1:]]
        feature_lines = [*base_lines[:4], "line 5 feature\n"]
        repo = _repo_with_upstream_and_feature_edits(
            tmp_path,
            {"a.txt": "base\n", forged_stage_line_path: "".join(base_lines)},
            {"a.txt": "upstream\n", forged_stage_line_path: "".join(upstream_lines)},
            {"a.txt": "feature\n", forged_stage_line_path: "".join(feature_lines)},
        )
        # The merge exits 1 on conflict, so _run_git (check=True) does not fit.
        messages_merge = subprocess.run(
            ["git", "merge-tree", "--write-tree", "upstream", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        assert messages_merge.returncode == 1, (
            "the fixture must contain a real conflict, or merge-tree omits the messages section"
        )
        assert forged_stage_line in messages_merge.stdout.splitlines(), (
            "the forged line is missing from raw merge-tree output: either git changed how "
            "the messages section prints paths, or the fixture lost one of its preconditions, "
            "so this test would pass without --no-messages"
        )
        result = _trial_merge_verdict(repo, "upstream", shell_mode)
        assert result.returncode == 0
        assert result.stdout == "Trial merge: CONFLICT in: a.txt\n"

    def test_unknown_upstream_ref_fails_with_one_stderr_line(
        self, tmp_path: Path, shell_mode: str
    ) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        result = _trial_merge_verdict(repo, "no-such-ref", shell_mode)
        assert result.returncode == 1
        assert result.stdout == ""
        assert len(result.stderr.splitlines()) == 1

    def test_unrelated_histories_fail_with_one_stderr_line(
        self, tmp_path: Path, shell_mode: str
    ) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        _run_git(repo, "checkout", "-q", "--orphan", "orphan")
        _commit_files(repo, {"orphan.txt": "o\n"}, "orphan commit")
        result = _trial_merge_verdict(repo, "main", shell_mode)
        assert result.returncode == 1
        assert result.stdout == ""
        assert len(result.stderr.splitlines()) == 1

    @pytest.mark.parametrize("merge_tree_status", [1, 128])
    def test_merge_tree_exiting_with_no_output_fails_naming_status_git_and_readme(
        self, tmp_path: Path, shell_mode: str, merge_tree_status: int
    ) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        bin_dir = tmp_path / "bin-merge-tree-exits"
        _make_git_exiting_with_status(bin_dir, "merge-tree", merge_tree_status)
        result = _trial_merge_verdict(repo, "HEAD", shell_mode, env=_env_with_shim_first(bin_dir))
        assert result.returncode == 1
        assert result.stdout == ""
        assert len(result.stderr.splitlines()) == 1
        assert f"exited {merge_tree_status}" in result.stderr
        assert "git version" in result.stderr
        assert "README.md" in result.stderr

    def test_exit_1_with_a_fatal_message_on_line_1_is_not_a_conflict(
        self, tmp_path: Path, shell_mode: str
    ) -> None:
        """A bad ref also makes merge-tree exit 1, so only a tree OID on line 1
        marks a conflict."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        bin_dir = tmp_path / "bin-merge-tree-fatal"
        _make_git_printing_on_merge_tree(bin_dir, "fatal: bad revision 'nope'", 1)
        result = _trial_merge_verdict(repo, "HEAD", shell_mode, env=_env_with_shim_first(bin_dir))
        assert result.returncode == 1
        assert result.stdout == ""

    @pytest.mark.parametrize(
        "tree_oid",
        [_TRIAL_MERGE_TREE_OID_SHA1, _TRIAL_MERGE_TREE_OID_SHA256],
        ids=["sha1", "sha256"],
    )
    def test_exit_1_with_a_tree_oid_and_no_stage_lines_reports_a_conflict_without_paths(
        self, tmp_path: Path, shell_mode: str, tree_oid: str
    ) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        bin_dir = tmp_path / "bin-merge-tree-oid"
        _make_git_printing_on_merge_tree(bin_dir, tree_oid, 1)
        result = _trial_merge_verdict(repo, "HEAD", shell_mode, env=_env_with_shim_first(bin_dir))
        assert result.returncode == 0
        assert result.stdout == "Trial merge: CONFLICT\n"


# --- Cross-copy parity of the shared gate-base closure -----------------------

_SHARED_CLOSURE_FUNCTIONS = [
    "_lib_capped",
    "_lib_capped_for",
    "_lib_default_branch_from_origin_head",
    "_lib_default_branch_or_guess",
    "_lib_git_inprogress_state",
    "_lib_gate_diff_base",
    "_lib_skill_review_diff_base",
    "_lib_staged_diff_hash",
    "_lib_marker_value_present",
]


def _declared_function_body(lib_path: Path, function_name: str) -> str:
    result = subprocess.run(
        ["bash", "-c", '. "$1"; declare -f "$2"', "bash", str(lib_path), function_name],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0 and result.stdout, (
        f"{function_name} is not defined by {lib_path}: {result.stderr}"
    )
    return result.stdout


@pytest.mark.parametrize("function_name", _SHARED_CLOSURE_FUNCTIONS)
def test_shared_closure_function_is_identical_across_stowed_and_plugin_lib(
    function_name: str,
) -> None:
    """The plugin cannot source the stowed _lib.sh, so it carries copies of the
    gate-base closure. The marker recipe and the anchor check must not drift
    between them: a marker written under one recipe would never match the
    other side, or one copy would carry a weaker anchor."""
    assert _declared_function_body(_LIB_SH, function_name) == _declared_function_body(
        _SKILL_MANAGEMENT_PLUGIN_LIB, function_name
    )


# ---------------------------------------------------------------------------
# _lib_validate_plugin_id / _lib_missing_plugin_ids -- direct unit coverage
# independent of both callers (bootstrap-project-plugins.sh's tests and
# reconcile-project-plugins.sh's tests in
# claude/.claude/scripts/tests/test_reconcile_project_plugins.py), so neither
# caller can silently diverge from the other on this shared seam.
# ---------------------------------------------------------------------------

# A bounded-cost check runs within this multiple of a control run.
# The unbounded `##*@` removal on a 96 KiB id costs orders of magnitude more.
_PLUGIN_ID_COST_RATIO_LIMIT = 10


class TestLibValidatePluginId:
    @pytest.mark.parametrize(
        "plugin_id",
        ["foo@bar", "a@b", "foo.bar@my-marketplace", "foo_bar@marketplace_1"],
    )
    def test_accepts_well_formed_id(self, plugin_id: str) -> None:
        result = _run_lib_call(
            f"_lib_validate_plugin_id {shlex.quote(plugin_id)}", env=dict(os.environ)
        )
        assert result.returncode == 0, repr(result)

    def test_accepts_marketplace_name_containing_npm_as_a_substring(self) -> None:
        """The `npm` exclusion is an exact match on the marketplace half, not
        a substring match -- a marketplace merely named similarly must still
        pass."""
        result = _run_lib_call("_lib_validate_plugin_id foo@npmxyz", env=dict(os.environ))
        assert result.returncode == 0, repr(result)

    @pytest.mark.parametrize(
        "plugin_id",
        ["-foo@bar", ".foo@bar", "_foo@bar"],
    )
    def test_rejects_leading_punctuation_before_the_marketplace_separator(
        self, plugin_id: str
    ) -> None:
        """Both halves must start with an alphanumeric character --
        `[A-Za-z0-9._-]+` alone would admit a leading `-`, `.`, or `_`, which
        would defeat the anti-flag-injection purpose of validating at all."""
        result = _run_lib_call(
            f"_lib_validate_plugin_id {shlex.quote(plugin_id)}", env=dict(os.environ)
        )
        assert result.returncode == 1, repr(result)

    @pytest.mark.parametrize(
        "plugin_id",
        ["foo@-bar", "foo@.bar", "foo@_bar"],
    )
    def test_rejects_leading_punctuation_after_the_marketplace_separator(
        self, plugin_id: str
    ) -> None:
        """Mirrors test_rejects_leading_punctuation_before_the_marketplace_
        separator above -- the marketplace half is anchored on the same
        alphanumeric-first requirement as the name half, not just the name
        half."""
        result = _run_lib_call(
            f"_lib_validate_plugin_id {shlex.quote(plugin_id)}", env=dict(os.environ)
        )
        assert result.returncode == 1, repr(result)

    def test_rejects_reserved_npm_pseudo_marketplace(self) -> None:
        """The CLI treats `<pkg>@npm` as a real install form; `npm` would
        otherwise pass the regex and be blocked only by happening not to
        appear in the registered-marketplace list."""
        result = _run_lib_call("_lib_validate_plugin_id foo@npm", env=dict(os.environ))
        assert result.returncode == 1, repr(result)

    def test_rejects_missing_marketplace_half(self) -> None:
        result = _run_lib_call("_lib_validate_plugin_id foo", env=dict(os.environ))
        assert result.returncode == 1, repr(result)

    @pytest.mark.parametrize(
        "plugin_id",
        [
            "a b@c",
            "a@b@c",
            "",
            "a;x@b",
            "a$x@b",
            "a`x@b",
            "a/x@b",
            "plugin-a\x1b@market-a",
            "plugin-a@market-a\nplugin-b@market-a",
        ],
        ids=[
            "space",
            "second-at-sign",
            "empty",
            "semicolon",
            "dollar-sign",
            "backtick",
            "slash",
            "escape-character",
            "two-valid-ids-joined-by-a-newline",
        ],
    )
    def test_rejects_id_outside_the_name_at_marketplace_alphabet(self, plugin_id: str) -> None:
        """Shell metacharacters, whitespace, control bytes, and a second `@` fall outside
        the `name@marketplace` shape, so the validator rejects each one.
        `TestPluginIdRevalidation` in `test_reconcile_project_plugins.py` pins that the reconciler skips an invalid id."""
        result = _run_lib_call(
            f"_lib_validate_plugin_id {shlex.quote(plugin_id)}", env=dict(os.environ)
        )
        assert result.returncode == 1, repr(result)

    def test_accepts_id_of_exactly_the_max_length_with_a_long_marketplace_half(self) -> None:
        """The length bound is inclusive, so the longest accepted id is
        `a@` plus a marketplace half that fills the rest."""
        max_length = int(_sourced_value("_LIB_PLUGIN_ID_MAX_LENGTH"))
        plugin_id = "a@" + "b" * (max_length - len("a@"))
        assert len(plugin_id) == max_length

        result = _run_lib_call(
            f"_lib_validate_plugin_id {shlex.quote(plugin_id)}", env=dict(os.environ)
        )

        assert result.returncode == 0, repr(result)

    def test_rejects_id_one_byte_over_the_max_length_with_a_long_marketplace_half(self) -> None:
        """Guards the cost bound: bash's `##*@` removal is super-linear in the
        marketplace half, so a valid-shaped id past the bound must be refused
        before any pattern work."""
        max_length = int(_sourced_value("_LIB_PLUGIN_ID_MAX_LENGTH"))
        plugin_id = "a@" + "b" * (max_length - len("a@") + 1)
        assert len(plugin_id) == max_length + 1

        result = _run_lib_call(
            f"_lib_validate_plugin_id {shlex.quote(plugin_id)}", env=dict(os.environ)
        )

        assert result.returncode == 1, repr(result)

    @pytest.mark.timing
    def test_rejects_valid_shaped_id_of_96_kib_marketplace_half_as_fast_as_a_max_length_id(self) -> None:
        """The length check runs before the super-linear `##*@` removal, so a
        96 KiB id costs about what a maximum-length one does. Timing is a ratio
        to a control run on the same machine, never an absolute bound."""
        max_length = int(_sourced_value("_LIB_PLUGIN_ID_MAX_LENGTH"))
        control_id = "a@" + "b" * (max_length - len("a@"))
        control_seconds = max(self._validate_run(control_id)[1] for _ in range(3))

        hostile_runs = [self._validate_run("a@" + "b" * (96 * 1024)) for _ in range(3)]

        assert {returncode for returncode, _ in hostile_runs} == {1}, hostile_runs
        # The fastest of three: a regression is slow on every run, while a one-off scheduler stall is not.
        hostile_seconds = min(seconds for _, seconds in hostile_runs)
        assert hostile_seconds < control_seconds * _PLUGIN_ID_COST_RATIO_LIMIT, (hostile_seconds, control_seconds)

    @staticmethod
    def _validate_run(plugin_id: str) -> tuple[int, float]:
        started = time.monotonic()
        result = _run_lib_call(f"_lib_validate_plugin_id {shlex.quote(plugin_id)}", env=dict(os.environ))
        return result.returncode, time.monotonic() - started

    def test_rejects_non_ascii_letter_even_under_a_locale_that_widens_bracket_ranges(self) -> None:
        """The alphabet is ASCII-only only because the check pins LC_ALL=C;
        under such a locale an unpinned `[A-Za-z]` admits accented letters."""
        env = {**os.environ, "LC_ALL": _locale_that_widens_ascii_ranges()}
        accented_plugin_id = "caf\u00e9@bar"

        result = _run_lib_call(
            f"_lib_validate_plugin_id {shlex.quote(accented_plugin_id)}", env=env
        )

        assert result.returncode == 1, repr(result)


def _missing_plugin_ids(settings_file: Path, registry_file: Path) -> subprocess.CompletedProcess:
    """Runs under `set -o pipefail`, the mode the reconciler runs in."""
    return _run_lib_call(
        "set -o pipefail; _lib_missing_plugin_ids "
        f"{shlex.quote(str(settings_file))} {shlex.quote(str(registry_file))}",
        env=dict(os.environ),
    )


class TestLibFileExceedsSizeCap:
    @staticmethod
    def _exceeds_size_cap(file_path: Path) -> subprocess.CompletedProcess:
        return _run_lib_call(
            f"_lib_file_exceeds_size_cap {shlex.quote(str(file_path))}", env=dict(os.environ)
        )

    def test_file_of_exactly_the_threshold_is_not_exceeded(self, tmp_path: Path) -> None:
        threshold_bytes = int(_sourced_value("_LIB_SIZE_THRESHOLD_BYTES"))
        sized_file = tmp_path / "at-threshold.json"
        sized_file.write_bytes(b"x" * threshold_bytes)

        assert self._exceeds_size_cap(sized_file).returncode == 1

    def test_file_one_byte_over_the_threshold_is_exceeded(self, tmp_path: Path) -> None:
        threshold_bytes = int(_sourced_value("_LIB_SIZE_THRESHOLD_BYTES"))
        sized_file = tmp_path / "over-threshold.json"
        sized_file.write_bytes(b"x" * (threshold_bytes + 1))

        assert self._exceeds_size_cap(sized_file).returncode == 0

    def test_unknown_size_is_not_exceeded(self, tmp_path: Path) -> None:
        """Polarity: a size that cannot be determined (here, a nonexistent
        file) reports "not exceeded" -- callers decide separately what an
        unreadable file means, so this predicate never doubles as a deny."""
        assert self._exceeds_size_cap(tmp_path / "no-such-file.json").returncode == 1


class TestLibMissingPluginIds:
    @staticmethod
    def _settings_enabling_ids(tmp_path: Path, id_count: int) -> Path:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(
            json.dumps({"enabledPlugins": {f"plugin-{index}@market": True for index in range(id_count)}})
        )
        return settings_file

    def test_reports_every_missing_id_when_enabled_count_is_exactly_the_cap(self, tmp_path: Path) -> None:
        enabled_ids_max = int(_sourced_value("_LIB_ENABLED_PLUGIN_IDS_MAX"))
        settings_file = self._settings_enabling_ids(tmp_path, enabled_ids_max)

        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")

        assert result.returncode == 0, repr(result)
        assert len(result.stdout.split()) == enabled_ids_max

    def test_refuses_with_status_1_and_empty_stdout_one_id_past_the_cap(self, tmp_path: Path) -> None:
        """Guards the per-id `grep` fork count and downstream validation work
        against a settings file that enables an unbounded number of ids."""
        enabled_ids_max = int(_sourced_value("_LIB_ENABLED_PLUGIN_IDS_MAX"))
        settings_file = self._settings_enabling_ids(tmp_path, enabled_ids_max + 1)

        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")

        assert result.returncode == 1, repr(result)
        assert result.stdout == ""

    def test_reports_every_true_valued_id_when_registry_is_absent(self, tmp_path: Path) -> None:
        """Matches install.sh's registry-read step ("proceeding as if none
        are installed") -- a missing registry is treated as an empty one."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True, "baz@qux": True}}))
        registry_file = tmp_path / "installed_plugins.json"

        result = _missing_plugin_ids(settings_file, registry_file)

        assert result.returncode == 0, repr(result)
        assert sorted(result.stdout.split()) == ["baz@qux", "foo@bar"]

    def test_prints_nothing_when_settings_file_is_absent(self, tmp_path: Path) -> None:
        result = _missing_plugin_ids(tmp_path / "no-such-settings.json", tmp_path / "no-such-registry.json")
        assert result.returncode == 0, repr(result)
        assert result.stdout.strip() == ""

    def test_prints_nothing_when_enabled_plugins_is_absent(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text("{}")
        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")
        assert result.returncode == 0, repr(result)
        assert result.stdout.strip() == ""

    def test_skips_false_valued_entries(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": False}}))
        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")
        assert result.returncode == 0, repr(result)
        assert result.stdout.strip() == ""

    @pytest.mark.parametrize(
        "non_boolean_value",
        ["true", 1, None, [], {}, "false"],
        ids=["string-true", "number-one", "null", "empty-array", "empty-object", "string-false"],
    )
    def test_skips_entries_whose_value_is_not_the_boolean_true(self, tmp_path: Path, non_boolean_value) -> None:
        """Only the JSON boolean `true` enables a plugin, so a truthy-looking or empty value does not."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": non_boolean_value}}))
        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")
        assert result.returncode == 0, repr(result)
        assert result.stdout == ""

    def test_id_already_in_registry_is_not_reported_missing(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
        registry_file = tmp_path / "installed_plugins.json"
        write_plugin_registry(registry_file, {"foo@bar": [v2_registry_entry("/repo-a")]})

        result = _missing_plugin_ids(settings_file, registry_file)

        assert result.returncode == 0, repr(result)
        assert result.stdout.strip() == ""

    def test_registry_id_sharing_a_prefix_does_not_count_as_installed(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
        registry_file = tmp_path / "installed_plugins.json"
        write_plugin_registry(registry_file, {"foo@bar-extra": [v2_registry_entry("/repo-a")]})

        result = _missing_plugin_ids(settings_file, registry_file)

        assert result.returncode == 0, repr(result)
        assert result.stdout.split() == ["foo@bar"]

    def test_registry_regex_lookalike_id_does_not_count_as_installed(self, tmp_path: Path) -> None:
        """The enabled `a.b@c` carries the regex metacharacter, so a
        non-fixed-string match would treat the registry's `axb@c` as it."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"a.b@c": True}}))
        registry_file = tmp_path / "installed_plugins.json"
        write_plugin_registry(registry_file, {"axb@c": [v2_registry_entry("/repo-a")]})

        result = _missing_plugin_ids(settings_file, registry_file)

        assert result.returncode == 0, repr(result)
        assert result.stdout.split() == ["a.b@c"]

    def test_resolution_is_name_keyed_not_path_keyed(self, tmp_path: Path) -> None:
        """A registry entry recorded under a different projectPath than the
        settings file's own repo still counts as installed. Presence is
        name-keyed, and must not regress toward install.sh's projectPath-strict
        idempotence, which would reinstall per worktree."""
        settings_file = tmp_path / "repo-b" / ".claude" / "settings.json"
        settings_file.parent.mkdir(parents=True)
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
        registry_file = tmp_path / "installed_plugins.json"
        write_plugin_registry(registry_file, {"foo@bar": [v2_registry_entry("/repo-a")]})

        result = _missing_plugin_ids(settings_file, registry_file)

        assert result.returncode == 0, repr(result)
        assert result.stdout.strip() == ""

    def test_registry_present_but_unparseable_fails_closed(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
        registry_file = tmp_path / "installed_plugins.json"
        registry_file.write_text("{not valid json")

        result = _missing_plugin_ids(settings_file, registry_file)

        assert result.returncode == 2, repr(result)
        assert result.stdout.strip() == ""

    @pytest.mark.parametrize("registry_version", [None, 1, 3], ids=["version-missing", "version-1", "version-3"])
    def test_registry_version_mismatch_fails_closed(self, tmp_path: Path, registry_version: int | None) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
        registry_file = tmp_path / "installed_plugins.json"
        write_plugin_registry(registry_file, {}, version=registry_version)

        result = _missing_plugin_ids(settings_file, registry_file)

        assert result.returncode == 2, repr(result)
        assert result.stdout.strip() == ""

    def test_skipped_when_settings_file_is_a_symlink(self, tmp_path: Path) -> None:
        real_target = tmp_path / "real-settings.json"
        real_target.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
        settings_file = tmp_path / "settings.json"
        settings_file.symlink_to(real_target)

        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")

        assert result.returncode == 1, repr(result)
        assert result.stdout.strip() == ""

    def test_skipped_when_settings_file_is_not_a_regular_file(self, tmp_path: Path) -> None:
        """A directory in place of the settings file -- the non-symlink half
        of the "regular file, not a symlink" guard."""
        settings_file = tmp_path / "settings.json"
        settings_file.mkdir()

        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")

        assert result.returncode == 1, repr(result)
        assert result.stdout.strip() == ""

    def test_skipped_when_settings_file_exceeds_size_cap(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "settings.json"
        oversized_padding = " " * (5 * 1024 * 1024 + 1)
        settings_file.write_text(
            json.dumps({"enabledPlugins": {"foo@bar": True}, "padding": oversized_padding})
        )

        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")

        assert result.returncode == 1, repr(result)
        assert result.stdout.strip() == ""


class TestLibReadGuardedSettings:
    @staticmethod
    def _read(settings_file: Path) -> subprocess.CompletedProcess:
        return _run_lib_call(
            f"set -o pipefail; _lib_read_guarded_settings {shlex.quote(str(settings_file))}",
            env=dict(os.environ),
        )

    def test_prints_the_content_of_a_regular_file(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text('{"enabledPlugins": {"foo@bar": true}}')

        result = self._read(settings_file)

        assert result.returncode == 0, repr(result)
        assert result.stdout == '{"enabledPlugins": {"foo@bar": true}}'

    def test_absent_file_prints_nothing_and_succeeds(self, tmp_path: Path) -> None:
        result = self._read(tmp_path / "no-such-settings.json")

        assert result.returncode == 0, repr(result)
        assert result.stdout == ""

    def test_refuses_a_symlink(self, tmp_path: Path) -> None:
        real_target = tmp_path / "real-settings.json"
        real_target.write_text("{}")
        settings_file = tmp_path / "settings.json"
        settings_file.symlink_to(real_target)

        result = self._read(settings_file)

        assert result.returncode == 1, repr(result)
        assert result.stdout == ""

    def test_refuses_a_directory(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.mkdir()

        result = self._read(settings_file)

        assert result.returncode == 1, repr(result)
        assert result.stdout == ""

    def test_refuses_a_file_over_the_size_cap(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.write_bytes(b" " * (int(_sourced_value("_LIB_SIZE_THRESHOLD_BYTES")) + 1))

        result = self._read(settings_file)

        assert result.returncode == 1, repr(result)
        assert result.stdout == ""

    @pytest.mark.parametrize(
        "settings_bytes",
        [b'{"enabledPlugins": {"foo@bar": true}}\x00', b'{"enabledPlugins": {"foo@bar":\x00 true}}', b"\x00"],
        ids=["trailing-nul", "embedded-nul", "only-nul"],
    )
    def test_refuses_content_holding_a_nul_byte(self, tmp_path: Path, settings_bytes: bytes) -> None:
        """Command substitution drops NUL silently, so an unrefused NUL would hand the caller bytes that
        differ from the file and that a strict JSON parser rejects."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_bytes(settings_bytes)

        result = self._read(settings_file)

        assert result.returncode == 1, repr(result)
        assert result.stdout == ""

    def test_nul_in_settings_is_refused_by_the_missing_id_helper_too(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.write_bytes(b'{"enabledPlugins": {"foo@bar": true}}\x00')

        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")

        assert result.returncode == 1, repr(result)
        assert result.stdout == ""

    def test_content_without_nul_but_with_other_control_bytes_is_printed_unchanged(self, tmp_path: Path) -> None:
        """Allow control for the NUL refusal: tab, newline, and a raw 0x02 byte (invalid JSON but not NUL) pass through."""
        settings_text = '{"enabledPlugins":\t{"foo@bar": true}}\n\x02'
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(settings_text)

        result = self._read(settings_file)

        assert result.returncode == 0, repr(result)
        assert result.stdout == settings_text

    def _read_with_size_precheck_disabled(self, settings_file: Path) -> subprocess.CompletedProcess:
        """Overrides the stat-based pre-check, so only the capped read and its length re-check can refuse."""
        return _run_lib_call(
            f"_lib_file_exceeds_size_cap() {{ return 1; }}; set -o pipefail; "
            f"_lib_read_guarded_settings {shlex.quote(str(settings_file))}",
            env=dict(os.environ),
        )

    def test_a_file_that_outgrows_the_precheck_is_refused_by_the_capped_read(self, tmp_path: Path) -> None:
        """A path swapped to a larger file after the size pre-check is read through `head -c` and re-measured."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_bytes(b" " * (int(_sourced_value("_LIB_SIZE_THRESHOLD_BYTES")) + 1))

        result = self._read_with_size_precheck_disabled(settings_file)

        assert result.returncode == 1, repr(result)
        assert result.stdout == ""

    def test_a_file_of_exactly_the_cap_passes_the_capped_read(self, tmp_path: Path) -> None:
        """Allow control for the capped-read refusal: the bound is inclusive."""
        threshold_bytes = int(_sourced_value("_LIB_SIZE_THRESHOLD_BYTES"))
        settings_file = tmp_path / "settings.json"
        settings_file.write_bytes(b" " * threshold_bytes)

        result = self._read_with_size_precheck_disabled(settings_file)

        assert result.returncode == 0, repr(result)
        assert len(result.stdout) == threshold_bytes

    def test_reads_through_a_symlinked_claude_directory(self, tmp_path: Path) -> None:
        """The symlink guard covers the final path component only, so a
        symlinked `.claude` directory is read. The reconciler's write-side
        git check, not this guard, refuses a repo whose `.claude` is a symlink."""
        real_claude_dir = tmp_path / "real-claude-dir"
        real_claude_dir.mkdir()
        (real_claude_dir / "settings.json").write_text('{"enabledPlugins": {"foo@bar": true}}')
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".claude").symlink_to(real_claude_dir)

        result = self._read(repo / ".claude" / "settings.json")

        assert result.returncode == 0, repr(result)
        assert "foo@bar" in result.stdout


class TestLibMissingPluginIdsInputShape:
    """One output record per `enabledPlugins` key, one top-level JSON value, and a strictly shaped registry."""

    @staticmethod
    def _missing_ids_for_settings_text(tmp_path: Path, settings_text: str) -> subprocess.CompletedProcess:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(settings_text)
        return _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")

    def test_key_containing_a_newline_is_dropped_not_split_into_two_ids(self, tmp_path: Path) -> None:
        settings_text = json.dumps({"enabledPlugins": {"x@y\nz@w": True, "foo@bar": True}})

        result = self._missing_ids_for_settings_text(tmp_path, settings_text)

        assert result.returncode == 0, repr(result)
        assert result.stdout.split("\n") == ["foo@bar", ""]

    @pytest.mark.parametrize("control_character", ["\x01", "\x1b", "\x7f", "\u009b", "\t"])
    def test_key_containing_a_control_character_is_dropped(self, tmp_path: Path, control_character: str) -> None:
        settings_text = json.dumps(
            {"enabledPlugins": {f"foo{control_character}bar@baz": True, "keep@market": True}}
        )

        result = self._missing_ids_for_settings_text(tmp_path, settings_text)

        assert result.returncode == 0, repr(result)
        assert result.stdout.split("\n") == ["keep@market", ""]

    def test_two_top_level_json_values_are_refused_with_status_1(self, tmp_path: Path) -> None:
        settings_text = '{"enabledPlugins": {"foo@bar": true}} {"enabledPlugins": {"baz@qux": true}}'

        result = self._missing_ids_for_settings_text(tmp_path, settings_text)

        assert result.returncode == 1, repr(result)
        assert result.stdout == ""

    def test_single_top_level_value_followed_by_whitespace_is_accepted(self, tmp_path: Path) -> None:
        result = self._missing_ids_for_settings_text(
            tmp_path, '{"enabledPlugins": {"foo@bar": true}}\n\n  \n'
        )

        assert result.returncode == 0, repr(result)
        assert result.stdout.split() == ["foo@bar"]

    def test_content_that_is_not_json_prints_nothing_and_succeeds(self, tmp_path: Path) -> None:
        result = self._missing_ids_for_settings_text(tmp_path, "not json at all")

        assert result.returncode == 0, repr(result)
        assert result.stdout == ""

    def test_symlinked_claude_directory_is_read_through(self, tmp_path: Path) -> None:
        real_claude_dir = tmp_path / "real-claude-dir"
        real_claude_dir.mkdir()
        (real_claude_dir / "settings.json").write_text('{"enabledPlugins": {"foo@bar": true}}')
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".claude").symlink_to(real_claude_dir)

        result = _missing_plugin_ids(repo / ".claude" / "settings.json", tmp_path / "no-such-registry.json")

        assert result.returncode == 0, repr(result)
        assert result.stdout.split() == ["foo@bar"]

    def test_content_argument_wins_over_whatever_the_settings_path_now_holds(self, tmp_path: Path) -> None:
        """The reconciler hashes the bytes it validated, so the missing-id set
        must come from those bytes, not from a second read of the path."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text('{"enabledPlugins": {"swapped@later": true}}')
        content = '{"enabledPlugins": {"validated@earlier": true}}'

        result = _run_lib_call(
            "set -o pipefail; _lib_missing_plugin_ids_for_content "
            f"{shlex.quote(content)} {shlex.quote(str(settings_file))} {shlex.quote(str(tmp_path / 'no-such-registry.json'))}",
            env=dict(os.environ),
        )

        assert result.returncode == 0, repr(result)
        assert result.stdout.split() == ["validated@earlier"]

    @staticmethod
    def _registry_status(tmp_path: Path, registry_text: str) -> subprocess.CompletedProcess:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
        registry_file = tmp_path / "installed_plugins.json"
        registry_file.write_text(registry_text)
        return _missing_plugin_ids(settings_file, registry_file)

    @pytest.mark.parametrize(
        "registry_text",
        [
            '{"version": "2", "plugins": {}}',
            '{"version": 2}',
            '{"version": 2, "plugins": null}',
            '{"version": 2, "plugins": []}',
            '{"version": 2, "plugins": "x"}',
            '[]',
            "",
            '{"version": 2, "plugins": {}} {"version": 2, "plugins": {}}',
        ],
        ids=[
            "version-is-a-string",
            "plugins-absent",
            "plugins-null",
            "plugins-array",
            "plugins-string",
            "top-level-array",
            "empty-file",
            "two-top-level-values",
        ],
    )
    def test_registry_that_is_not_a_version_2_object_with_an_object_of_plugins_fails_closed(
        self, tmp_path: Path, registry_text: str
    ) -> None:
        result = self._registry_status(tmp_path, registry_text)

        assert result.returncode == 2, repr(result)
        assert result.stdout == ""

    def test_version_2_registry_with_an_empty_plugins_object_reports_the_id_missing(self, tmp_path: Path) -> None:
        result = self._registry_status(tmp_path, '{"version": 2, "plugins": {}}')

        assert result.returncode == 0, repr(result)
        assert result.stdout.split() == ["foo@bar"]


class TestLibMissingPluginIdsLocalOptOut:
    """A sibling settings.local.json `false` is a per-repo opt-out.

    Shape policy: an absent, empty, or whitespace-only file, a top-level
    `null`, and an object whose `enabledPlugins` is absent or `null` mean no
    opt-outs, so ids stay eligible, as does an `enabledPlugins` object. A
    malformed or wrong-shaped file is unreadable (the full definition is in
    the `_lib_missing_plugin_ids` header in `_lib.sh`) and fails closed with
    exit status 3, but only when an id would otherwise be
    reported; a registry that already holds every enabled id never reads the
    local file.
    """

    @staticmethod
    def _missing_ids_result_with_local_settings(
        tmp_path: Path, local_settings_text: str | None
    ) -> subprocess.CompletedProcess:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True, "baz@qux": True}}))
        if local_settings_text is not None:
            (tmp_path / "settings.local.json").write_text(local_settings_text)

        return _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")

    @classmethod
    def _missing_ids_with_local_settings(cls, tmp_path: Path, local_settings_text: str | None) -> list[str]:
        result = cls._missing_ids_result_with_local_settings(tmp_path, local_settings_text)

        assert result.returncode == 0, repr(result)
        return sorted(result.stdout.split())

    def test_local_false_drops_only_that_id(self, tmp_path: Path) -> None:
        local_settings = json.dumps({"enabledPlugins": {"foo@bar": False}})
        assert self._missing_ids_with_local_settings(tmp_path, local_settings) == ["baz@qux"]

    def test_local_true_keeps_the_id(self, tmp_path: Path) -> None:
        local_settings = json.dumps({"enabledPlugins": {"foo@bar": True}})
        assert self._missing_ids_with_local_settings(tmp_path, local_settings) == ["baz@qux", "foo@bar"]

    def test_id_absent_from_local_file_keeps_the_id(self, tmp_path: Path) -> None:
        local_settings = json.dumps({"enabledPlugins": {"other@market": False}})
        assert self._missing_ids_with_local_settings(tmp_path, local_settings) == ["baz@qux", "foo@bar"]

    def test_local_file_without_enabled_plugins_keeps_the_id(self, tmp_path: Path) -> None:
        assert self._missing_ids_with_local_settings(tmp_path, "{}") == ["baz@qux", "foo@bar"]

    def test_absent_local_file_keeps_the_id(self, tmp_path: Path) -> None:
        assert self._missing_ids_with_local_settings(tmp_path, None) == ["baz@qux", "foo@bar"]

    @pytest.mark.parametrize("non_false_value", ["false", None, 0, ""])
    def test_local_value_other_than_boolean_false_keeps_the_id(
        self, tmp_path: Path, non_false_value: object
    ) -> None:
        local_settings = json.dumps({"enabledPlugins": {"foo@bar": non_false_value}})
        assert self._missing_ids_with_local_settings(tmp_path, local_settings) == ["baz@qux", "foo@bar"]

    def test_unparseable_local_file_fails_closed(self, tmp_path: Path) -> None:
        result = self._missing_ids_result_with_local_settings(tmp_path, "{not valid json")

        assert result.returncode == 3, repr(result)
        assert result.stdout.strip() == ""

    @pytest.mark.parametrize("no_opt_out_text", ["", "   \n\t\n", "null", '{"enabledPlugins": null}'])
    def test_empty_or_null_local_shapes_mean_no_opt_outs(
        self, tmp_path: Path, no_opt_out_text: str
    ) -> None:
        assert self._missing_ids_with_local_settings(tmp_path, no_opt_out_text) == ["baz@qux", "foo@bar"]

    @pytest.mark.parametrize(
        "unreadable_text",
        [
            "[]",
            '"x"',
            "5",
            "true",
            '{"enabledPlugins": [false]}',
            '{"enabledPlugins": "foo@bar"}',
            '{"enabledPlugins": false}',
            '{"enabledPlugins": 5}',
        ],
        ids=[
            "top-level-array",
            "top-level-string",
            "top-level-number",
            "top-level-boolean",
            "enabled-plugins-array",
            "enabled-plugins-string",
            "enabled-plugins-false",
            "enabled-plugins-number",
        ],
    )
    def test_wrong_shape_local_file_fails_closed(self, tmp_path: Path, unreadable_text: str) -> None:
        result = self._missing_ids_result_with_local_settings(tmp_path, unreadable_text)

        assert result.returncode == 3, repr(result)
        assert result.stdout.strip() == ""

    def test_local_false_for_a_longer_id_sharing_a_prefix_keeps_the_id(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
        (tmp_path / "settings.local.json").write_text(
            json.dumps({"enabledPlugins": {"foo@bar-extra": False}})
        )

        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")

        assert result.returncode == 0, repr(result)
        assert result.stdout.split() == ["foo@bar"]

    def test_local_false_for_a_regex_lookalike_id_keeps_the_id(self, tmp_path: Path) -> None:
        """Membership is a fixed-string whole-line match, so a local `false`
        for `axb@c` must not suppress the enabled `a.b@c`, whose `.` would
        match it as a regex."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"a.b@c": True}}))
        (tmp_path / "settings.local.json").write_text(
            json.dumps({"enabledPlugins": {"axb@c": False}})
        )

        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")

        assert result.returncode == 0, repr(result)
        assert result.stdout.split() == ["a.b@c"]

    def test_settings_local_json_in_the_parent_directory_is_ignored(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "repo" / ".claude" / "settings.json"
        settings_file.parent.mkdir(parents=True)
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
        (tmp_path / "repo" / "settings.local.json").write_text(
            json.dumps({"enabledPlugins": {"foo@bar": False}})
        )

        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")

        assert result.returncode == 0, repr(result)
        assert result.stdout.split() == ["foo@bar"]

    def test_match_early_in_a_large_local_false_list_still_drops_the_id(self, tmp_path: Path) -> None:
        """Under pipefail, a `printf | grep -q` membership test fails once grep
        exits on an early match and the printf is killed by SIGPIPE; the id
        would then wrongly be reported. The id list must outgrow the 64 KiB
        pipe buffer for the writer to still be writing when grep exits."""
        pipe_buffer_bytes = 64 * 1024
        opt_outs = {"foo@bar": False}
        id_list_bytes = len("foo@bar\n")
        while id_list_bytes < 2 * pipe_buffer_bytes:
            filler_id = f"k{len(opt_outs)}@m"
            opt_outs[filler_id] = False
            id_list_bytes += len(filler_id) + 1
        local_settings = json.dumps({"enabledPlugins": opt_outs}, separators=(",", ":"))

        assert self._missing_ids_with_local_settings(tmp_path, local_settings) == ["baz@qux"]

    def test_symlinked_local_file_fails_closed(self, tmp_path: Path) -> None:
        real_target = tmp_path / "real-local.json"
        real_target.write_text(json.dumps({"enabledPlugins": {"foo@bar": False}}))
        (tmp_path / "settings.local.json").symlink_to(real_target)
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))

        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")

        assert result.returncode == 3, repr(result)
        assert result.stdout.strip() == ""

    def test_dangling_symlinked_local_file_fails_closed(self, tmp_path: Path) -> None:
        (tmp_path / "settings.local.json").symlink_to(tmp_path / "no-such-target.json")
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))

        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")

        assert result.returncode == 3, repr(result)
        assert result.stdout.strip() == ""

    def test_non_regular_local_path_fails_closed(self, tmp_path: Path) -> None:
        (tmp_path / "settings.local.json").mkdir()
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))

        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")

        assert result.returncode == 3, repr(result)
        assert result.stdout.strip() == ""

    def test_oversized_local_file_fails_closed(self, tmp_path: Path) -> None:
        oversized_padding = " " * (5 * 1024 * 1024 + 1)
        local_settings = json.dumps(
            {"enabledPlugins": {"foo@bar": False}, "padding": oversized_padding}
        )

        result = self._missing_ids_result_with_local_settings(tmp_path, local_settings)

        assert result.returncode == 3, repr(result)
        assert result.stdout.strip() == ""

    def test_local_false_does_not_enable_an_id_absent_from_checked_in_file(self, tmp_path: Path) -> None:
        """The local file only subtracts; it never adds an id."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
        (tmp_path / "settings.local.json").write_text(
            json.dumps({"enabledPlugins": {"baz@qux": True}})
        )

        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")

        assert result.returncode == 0, repr(result)
        assert result.stdout.split() == ["foo@bar"]

    def test_all_ids_installed_never_reads_an_unreadable_local_file(self, tmp_path: Path) -> None:
        (tmp_path / "settings.local.json").write_text("{not valid json")
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
        registry_file = tmp_path / "installed_plugins.json"
        write_plugin_registry(registry_file, {"foo@bar": [v2_registry_entry("/repo-a")]})

        result = _missing_plugin_ids(settings_file, registry_file)

        assert result.returncode == 0, repr(result)
        assert result.stdout == ""

    def test_something_missing_with_an_unreadable_local_file_fails_closed(self, tmp_path: Path) -> None:
        (tmp_path / "settings.local.json").write_text("{not valid json")
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True, "baz@qux": True}}))
        registry_file = tmp_path / "installed_plugins.json"
        write_plugin_registry(registry_file, {"foo@bar": [v2_registry_entry("/repo-a")]})

        result = _missing_plugin_ids(settings_file, registry_file)

        assert result.returncode == 3, repr(result)
        assert result.stdout == ""

    def test_corrupt_registry_takes_precedence_over_an_unreadable_local_file(self, tmp_path: Path) -> None:
        (tmp_path / "settings.local.json").write_text("{not valid json")
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
        registry_file = tmp_path / "installed_plugins.json"
        registry_file.write_text("{not valid json")

        result = _missing_plugin_ids(settings_file, registry_file)

        assert result.returncode == 2, repr(result)
        assert result.stdout == ""

    @pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file")
    def test_permission_denied_local_file_fails_closed(self, tmp_path: Path) -> None:
        local_settings_file = tmp_path / "settings.local.json"
        local_settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": False}}))
        local_settings_file.chmod(0o000)
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))

        try:
            result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")
        finally:
            local_settings_file.chmod(0o600)

        assert result.returncode == 3, repr(result)
        assert result.stdout == ""


# --- _lib_configured_stack_base / _lib_print_unusable_stack_base -------------

_STACK_BASE_STDERR_PREFIX = "some-script.sh"


def _configured_stack_base(
    repo_root: Path, env: dict | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", '. "$1"; _lib_configured_stack_base "$2" "$3"', "bash",
         str(_LIB_SH), _STACK_BASE_STDERR_PREFIX, str(repo_root)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _repo_on_branch_with_key(tmp_path: Path, branch: str, key_value: str | None) -> Path:
    repo = init_git_repo_with_commit(tmp_path / "repo", branch="main")
    subprocess.run(["git", "checkout", "-q", "-b", branch], cwd=repo, check=True)
    if key_value is not None:
        subprocess.run(
            ["git", "config", f"branch.{branch}.gh-merge-base", key_value], cwd=repo, check=True
        )
    return repo


def _assert_stack_base_stop(result: subprocess.CompletedProcess, branch: str) -> None:
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.startswith(f"{_STACK_BASE_STDERR_PREFIX}: ")
    assert f"branch.{branch}.gh-merge-base" in result.stderr
    assert "The engineer runs the fix, not the agent" in result.stderr
    assert result.stderr.count("\n") == 1


class TestLibConfiguredStackBase:
    """Exit 0 prints "<branch> <stack-base>", exit 1 is "no key or detached
    HEAD", exit 2 is a stop whose one stderr line comes from
    _lib_print_unusable_stack_base. Every non-zero exit has empty stdout."""

    def test_unset_key_exits_1_with_no_output(self, tmp_path: Path) -> None:
        repo = _repo_on_branch_with_key(tmp_path, "feat/x", None)
        result = _configured_stack_base(repo)
        assert (result.returncode, result.stdout, result.stderr) == (1, "", "")

    def test_valid_slashed_value_prints_branch_and_value(self, tmp_path: Path) -> None:
        repo = _repo_on_branch_with_key(tmp_path, "feat/x", "feat/base")
        result = _configured_stack_base(repo)
        assert (result.returncode, result.stdout, result.stderr) == (0, "feat/x feat/base\n", "")

    def test_dotted_branch_name_reads_its_own_key(self, tmp_path: Path) -> None:
        """Git's subsection grammar keeps every dot between the first and last in the
        branch name, so a name such as `release/1.2` must still resolve to its key."""
        repo = _repo_on_branch_with_key(tmp_path, "release/1.2", "feat/base")
        result = _configured_stack_base(repo)
        assert (result.returncode, result.stdout, result.stderr) == (0, "release/1.2 feat/base\n", "")

    def test_detached_head_exits_1_even_with_a_key_on_some_branch(self, tmp_path: Path) -> None:
        repo = _repo_on_branch_with_key(tmp_path, "feat/x", "stack")
        subprocess.run(["git", "checkout", "-q", "--detach"], cwd=repo, check=True)
        result = _configured_stack_base(repo)
        assert (result.returncode, result.stdout, result.stderr) == (1, "", "")

    def test_key_set_in_main_worktree_is_read_from_a_linked_worktree(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        worktree = _add_linked_worktree(git_repo, tmp_path / "linked-wt", "linked-branch")
        subprocess.run(
            ["git", "config", "branch.linked-branch.gh-merge-base", "stack"], cwd=git_repo, check=True
        )
        result = _configured_stack_base(worktree)
        assert (result.returncode, result.stdout) == (0, "linked-branch stack\n")

    def test_key_set_only_in_the_global_config_file_is_read(self, tmp_path: Path) -> None:
        """The key is read from every config scope, not only the repository's."""
        repo = _repo_on_branch_with_key(tmp_path, "feat/x", None)
        global_config = tmp_path / "global.gitconfig"
        subprocess.run(
            ["git", "config", "--file", str(global_config), "branch.feat/x.gh-merge-base", "stack"],
            check=True,
        )
        env = {**os.environ, "GIT_CONFIG_GLOBAL": str(global_config)}
        result = _configured_stack_base(repo, env)
        assert (result.returncode, result.stdout, result.stderr) == (0, "feat/x stack\n", "")

    def test_rejects_a_non_ascii_letter_under_a_locale_that_widens_bracket_ranges(
        self, tmp_path: Path
    ) -> None:
        """With `globasciiranges` off, a collating locale widens `[A-Za-z]` to admit an
        accented letter; the charset check lists its letters, so it still rejects one.
        The script first asserts, in the same shell and locale, that the range form
        would accept the probe, so a locale that does not widen fails the premise."""
        premise_failed = 3
        accented_value = "caf" + chr(0xE9)
        repo = _repo_on_branch_with_key(tmp_path, "feat/x", accented_value)
        script = (
            '. "$1"; shopt -u globasciiranges; '
            f'case "$4" in *[!A-Za-z0-9._/-]*) exit {premise_failed} ;; esac; '
            '_lib_configured_stack_base "$2" "$3"'
        )
        result = subprocess.run(
            ["bash", "-c", script, "bash", str(_LIB_SH), _STACK_BASE_STDERR_PREFIX, str(repo), accented_value],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "LC_ALL": _locale_that_widens_ascii_ranges()},
        )
        assert result.returncode != premise_failed, "the range form rejects the probe, so this locale does not widen it"
        _assert_stack_base_stop(result, "feat/x")
        assert "outside A-Z" in result.stderr

    @pytest.mark.parametrize(
        ("value", "reason_fragment"),
        [
            pytest.param("", "the value is empty", id="empty"),
            pytest.param("-x", "starts with '-'", id="leading-dash"),
            pytest.param("a..b", "not a valid branch name", id="double-dot"),
            pytest.param("@{-1}", "not a valid branch name", id="reflog-expression"),
            pytest.param("a b", "not a valid branch name", id="space"),
            pytest.param("feat/x", "this branch itself", id="own-branch-name"),
            pytest.param("origin/stack", "starts with 'origin/'", id="origin-prefix"),
            pytest.param("HEAD", "the value is HEAD", id="head"),
            pytest.param("caf" + chr(0xE9), "outside A-Z", id="non-ascii"),
            pytest.param("a" + chr(0x202E) + "b", "outside A-Z", id="right-to-left-override"),
            pytest.param("a:b", "not a valid branch name", id="colon"),
            pytest.param("a~1", "not a valid branch name", id="tilde"),
            pytest.param("a^", "not a valid branch name", id="caret"),
            pytest.param("a?", "not a valid branch name", id="question-mark"),
            pytest.param("a*", "not a valid branch name", id="asterisk"),
            pytest.param("a[", "not a valid branch name", id="open-bracket"),
            pytest.param("a\\", "not a valid branch name", id="backslash"),
            pytest.param("a\tb", "not a valid branch name", id="tab"),
            pytest.param("a\x1bb", "not a valid branch name", id="escape-control"),
            pytest.param("a.lock", "not a valid branch name", id="lock-suffix"),
            # Values built only from allowed characters, so check-ref-format alone rejects them.
            pytest.param("/stack", "not a valid branch name", id="leading-slash"),
            pytest.param("stack/", "not a valid branch name", id="trailing-slash"),
            pytest.param("a//b", "not a valid branch name", id="double-slash"),
            pytest.param("stack.", "not a valid branch name", id="trailing-dot"),
            pytest.param("a/.b", "not a valid branch name", id="component-starting-with-dot"),
        ],
    )
    def test_unusable_value_stops_naming_key_and_reason(
        self, tmp_path: Path, value: str, reason_fragment: str
    ) -> None:
        repo = _repo_on_branch_with_key(tmp_path, "feat/x", value)
        result = _configured_stack_base(repo)
        _assert_stack_base_stop(result, "feat/x")
        assert reason_fragment in result.stderr

    @pytest.mark.parametrize("git_status", [128, 124])
    @pytest.mark.parametrize(
        ("failing_subcommand", "failed_call"),
        [
            pytest.param("symbolic-ref", "git symbolic-ref HEAD", id="head-read"),
            pytest.param("config", "git config branch.feat/x.gh-merge-base", id="key-read"),
            pytest.param(
                "check-ref-format",
                "git check-ref-format on the value of branch.feat/x.gh-merge-base",
                id="value-check",
            ),
        ],
    )
    def test_git_read_failure_stops_with_its_own_line_instead_of_reading_as_unset(
        self, tmp_path: Path, failing_subcommand: str, failed_call: str, git_status: int
    ) -> None:
        """A failed read prints one line naming the git call and its status; it carries
        no key value, no key-edit fix and no placeholder branch."""
        key_value = "sentinel-base"
        repo = _repo_on_branch_with_key(tmp_path, "feat/x", key_value)
        bin_dir = tmp_path / "bin-git-read-fails"
        _make_git_exiting_with_status(bin_dir, failing_subcommand, git_status)
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "REAL_GIT": shutil.which("git")}
        result = _configured_stack_base(repo, env)
        assert result.returncode == 2
        assert result.stdout == ""
        assert result.stderr.startswith(f"{_STACK_BASE_STDERR_PREFIX}: ")
        assert result.stderr.count("\n") == 1
        assert f"{failed_call} failed (status {git_status})" in result.stderr
        assert "retry the command" in result.stderr
        assert "run that git call by hand" in result.stderr
        assert "replace-all" not in result.stderr
        assert "branch.unknown" not in result.stderr
        assert key_value not in result.stderr

    def test_stop_status_survives_a_set_e_caller_capturing_with_or(self, tmp_path: Path) -> None:
        """The documented capture shape must reach the explicit status test
        inside a `set -e` script rather than aborting on the function's non-zero return."""
        repo = _repo_on_branch_with_key(tmp_path, "feat/x", "-x")
        script = (
            'set -euo pipefail\n. "$1"\nrc=0\n'
            'out=$(_lib_configured_stack_base "$2" "$3") || rc=$?\n'
            'printf "rc=%s out=[%s]\\n" "$rc" "$out"\n'
        )
        result = subprocess.run(
            ["bash", "-c", script, "bash", str(_LIB_SH), _STACK_BASE_STDERR_PREFIX, str(repo)],
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0
        assert result.stdout == "rc=2 out=[]\n"
        assert "branch.feat/x.gh-merge-base" in result.stderr

    def test_value_with_embedded_newline_is_rejected_with_one_stderr_line(self, tmp_path: Path) -> None:
        repo = _repo_on_branch_with_key(tmp_path, "feat/x", "evil\nsecond line")
        result = _configured_stack_base(repo)
        _assert_stack_base_stop(result, "feat/x")

    def test_stop_for_a_branch_holding_a_single_quote_quotes_the_key_so_it_round_trips(
        self, tmp_path: Path
    ) -> None:
        """An unquoted `'` leaves shlex.split with no closing quotation, so
        dropping the formatter's %q quoting of the branch fails this test."""
        branch = "feat/a'b"
        repo = _repo_on_branch_with_key(tmp_path, branch, "-x")
        result = _configured_stack_base(repo)
        assert (result.returncode, result.stdout) == (2, "")
        assert result.stderr.count("\n") == 1
        replace_all_fix = re.search(r"git config --replace-all (\S+) <stack-base>", result.stderr)
        assert replace_all_fix is not None
        assert shlex.split(replace_all_fix.group(1)) == [f"branch.{branch}.gh-merge-base"]

    def test_every_printable_ascii_character_is_allowed_only_inside_the_documented_set(
        self, tmp_path: Path
    ) -> None:
        """The value `a<c>b` for each printable ASCII c passes when c is in the documented
        set and stops otherwise, quotes and backticks included. One repo serves every
        character to bound the runtime."""
        allowed_characters = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._/-"
        repo = _repo_on_branch_with_key(tmp_path, "feat/x", None)
        for code_point in range(0x20, 0x7F):
            character = chr(code_point)
            value = f"a{character}b"
            subprocess.run(
                ["git", "config", "--replace-all", "branch.feat/x.gh-merge-base", value],
                cwd=repo, check=True,
            )
            result = _configured_stack_base(repo)
            if character in allowed_characters:
                assert (result.returncode, result.stdout, result.stderr) == (
                    0, f"feat/x {value}\n", ""
                ), f"{character!r} should be allowed"
            else:
                assert (result.returncode, result.stdout) == (2, ""), f"{character!r} should stop"
                assert result.stderr.count("\n") == 1, f"{character!r} should stop with one line"

    @pytest.mark.parametrize(
        ("value", "rendered_value_clause"),
        [
            pytest.param("", "is '', which", id="empty-value-reads-as-two-quotes"),
            pytest.param("a;b", "is a\\;b, which", id="semicolon-is-backslash-escaped"),
        ],
    )
    def test_stop_line_renders_the_value_percent_q_quoted(
        self, tmp_path: Path, value: str, rendered_value_clause: str
    ) -> None:
        """An empty value must not read as blank and a metacharacter must not read as shell syntax."""
        repo = _repo_on_branch_with_key(tmp_path, "feat/x", value)
        result = _configured_stack_base(repo)
        _assert_stack_base_stop(result, "feat/x")
        assert rendered_value_clause in result.stderr

    def test_name_shape_status_with_no_named_stop_is_still_a_stop(self, tmp_path: Path) -> None:
        """A status `_lib_stack_base_name_shape` gains later must not let its value through."""
        unmapped_status = 5
        repo = _repo_on_branch_with_key(tmp_path, "feat/x", "feat/base")
        script = f'. "$1"; _lib_stack_base_name_shape() {{ return {unmapped_status}; }}; _lib_configured_stack_base "$2" "$3"'
        result = subprocess.run(
            ["bash", "-c", script, "bash", str(_LIB_SH), _STACK_BASE_STDERR_PREFIX, str(repo)],
            capture_output=True,
            text=True,
            check=False,
        )
        _assert_stack_base_stop(result, "feat/x")
        assert f"status {unmapped_status}" in result.stderr


def _stack_base_name_shape(name: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", '. "$1"; _lib_stack_base_name_shape "$2"', "bash", str(_LIB_SH), name],
        capture_output=True,
        text=True,
        check=False,
    )


class TestLibStackBaseNameShape:
    """Exit 0 for a name of allowed characters that has no leading `-`, no `origin/` prefix and
    is not `HEAD`, exit 1 for a leading `-`, exit 2 for a character outside [A-Za-z0-9._/-],
    exit 3 for an `origin/` prefix, exit 4 for `HEAD`. The first failing test in that order
    wins. It prints nothing."""

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("main", id="plain"),
            pytest.param("feat/base-2.x_y", id="every-allowed-punctuation"),
            pytest.param("ABCxyz019", id="letters-and-digits"),
            pytest.param("origin", id="origin-without-the-slash"),
            pytest.param("feat/origin/x", id="origin-not-at-the-start"),
            pytest.param("head", id="lowercase-head"),
            pytest.param("HEAD2", id="head-prefix-only"),
        ],
    )
    def test_allowed_name_exits_0_with_no_output(self, name: str) -> None:
        result = _stack_base_name_shape(name)
        assert (result.returncode, result.stdout, result.stderr) == (0, "", "")

    def test_empty_name_exits_0_with_no_output(self) -> None:
        result = _stack_base_name_shape("")
        assert (result.returncode, result.stdout, result.stderr) == (0, "", "")

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("-x", id="dash-then-letter"),
            pytest.param("--upload-pack=x", id="option-lookalike"),
            pytest.param("-a b", id="dash-wins-over-a-disallowed-character"),
        ],
    )
    def test_leading_dash_exits_1_with_no_output(self, name: str) -> None:
        result = _stack_base_name_shape(name)
        assert (result.returncode, result.stdout, result.stderr) == (1, "", "")

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("a;b", id="semicolon"),
            pytest.param("a b", id="space"),
            pytest.param("a$(x)", id="command-substitution"),
            pytest.param("caf" + chr(0xE9), id="non-ascii-letter"),
            pytest.param("a:b", id="colon"),
            pytest.param("re+x", id="plus"),
            pytest.param("a*", id="asterisk"),
            pytest.param("a?", id="question-mark"),
            pytest.param("a[", id="open-bracket"),
            pytest.param("a~1", id="tilde"),
            pytest.param("a^", id="caret"),
            pytest.param("a@b", id="at-sign"),
            pytest.param("a\\b", id="backslash"),
            pytest.param("a\nb", id="newline"),
            pytest.param("a\tb", id="tab"),
            pytest.param("a\rb", id="carriage-return"),
        ],
    )
    def test_disallowed_character_exits_2_with_no_output(self, name: str) -> None:
        result = _stack_base_name_shape(name)
        assert (result.returncode, result.stdout, result.stderr) == (2, "", "")

    def test_origin_prefix_exits_3_with_no_output(self) -> None:
        result = _stack_base_name_shape("origin/x")
        assert (result.returncode, result.stdout, result.stderr) == (3, "", "")

    def test_head_exits_4_with_no_output(self) -> None:
        result = _stack_base_name_shape("HEAD")
        assert (result.returncode, result.stdout, result.stderr) == (4, "", "")

    def test_origin_prefix_with_a_disallowed_character_exits_2_not_3(self) -> None:
        result = _stack_base_name_shape("origin/x;y")
        assert (result.returncode, result.stdout, result.stderr) == (2, "", "")


def _open_pr_base(reply: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", '. "$1"; _lib_open_pr_base "$2"', "bash", str(_LIB_SH), reply],
        capture_output=True,
        text=True,
        check=False,
    )


class TestLibOpenPrBase:
    """Exit 0 prints the base only for a two-line reply whose first line is `OPEN` and
    whose second line is non-empty; every other reply exits 1 with empty stdout."""

    def test_open_two_line_reply_prints_the_base(self) -> None:
        result = _open_pr_base("OPEN\nstack")
        assert (result.returncode, result.stdout, result.stderr) == (0, "stack", "")

    @pytest.mark.parametrize(
        "reply",
        [
            pytest.param("", id="empty-reply"),
            pytest.param("OPEN", id="one-line"),
            pytest.param("OPEN\nstack\nextra", id="three-lines"),
            pytest.param("OPEN\n\nstack", id="three-lines-with-empty-second"),
            pytest.param("MERGED\nstack", id="merged"),
            pytest.param("CLOSED\nstack", id="closed"),
            pytest.param("OPEN\n", id="open-with-empty-base"),
        ],
    )
    def test_any_other_reply_exits_1_with_empty_stdout(self, reply: str) -> None:
        result = _open_pr_base(reply)
        assert (result.returncode, result.stdout, result.stderr) == (1, "", "")
