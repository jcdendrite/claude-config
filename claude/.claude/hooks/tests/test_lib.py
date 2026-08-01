"""Unit tests for _lib.sh helpers.

Covers _lib_parse_tool_input_or_deny and _lib_jq, plus the marker-read helper
_lib_marker_value_present and the gate-release agent predicate
_lib_is_no_gate_release_agent.

The parse tests drive the helper via a throwaway shell harness that defines
emit_deny before sourcing _lib.sh (the canonical caller pattern), then calls
_lib_parse_tool_input_or_deny and reports either DENY:<msg> or OK:<tool>:<cmd>.
"""
from __future__ import annotations

import os
import re
import subprocess
import textwrap
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


# --- _lib_marker_value_present -------------------------------------------
#
# Completion markers are content-addressed: the stored value is the whole
# authorization, and the filename prefix only namespaces concurrent writers
# apart. These tests pin that read contract, plus the two shell-level
# properties the helper depends on — whole-line matching, and a zero-match
# glob reporting "not found" rather than erroring on an unexpanded pattern.


def _marker_value_present(
    markers_dir: Path, expected: str, *prefixes: str
) -> subprocess.CompletedProcess:
    """Run _lib_marker_value_present; returncode 0 means found."""
    argv = [
        "bash", "-c", f'. {_LIB_SH}; _lib_marker_value_present "$@"', "bash",
        str(markers_dir), expected, *prefixes,
    ]
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def _write_marker(
    markers_dir: Path, name: str, value: str, *, trailing_newline: bool = True
) -> None:
    markers_dir.mkdir(parents=True, exist_ok=True)
    (markers_dir / name).write_text(value + ("\n" if trailing_newline else ""))


def test_marker_value_present_matches_stored_value(tmp_path: Path) -> None:
    """A marker holding the expected value under a matching prefix is found."""
    _write_marker(tmp_path, "repohash.session-a", "deadbeef")
    assert _marker_value_present(tmp_path, "deadbeef", "repohash.").returncode == 0


def test_marker_value_present_matches_across_session_keys(tmp_path: Path) -> None:
    """The session suffix is not part of the read predicate.

    This is the defect the helper exists to close: a marker written under one
    session id must authorize a read from any other session, because the stored
    hash — not the filename — proves which state was reviewed.
    """
    _write_marker(tmp_path, "repohash.session-that-has-since-ended", "deadbeef")
    assert _marker_value_present(tmp_path, "deadbeef", "repohash.").returncode == 0


def test_marker_value_present_rejects_different_value(tmp_path: Path) -> None:
    """A marker under a matching prefix holding a different value is not a match."""
    _write_marker(tmp_path, "repohash.session-a", "deadbeef")
    assert _marker_value_present(tmp_path, "cafebabe", "repohash.").returncode != 0


def test_marker_value_present_tolerates_missing_trailing_newline(tmp_path: Path) -> None:
    """A stored value with no trailing newline still matches.

    grep treats a final unterminated line as a line, so the read side does not
    depend on the writer's newline discipline.
    """
    _write_marker(tmp_path, "repohash.session-a", "deadbeef", trailing_newline=False)
    assert _marker_value_present(tmp_path, "deadbeef", "repohash.").returncode == 0


def test_marker_value_present_scans_multiple_prefixes(tmp_path: Path) -> None:
    """A match under any supplied prefix counts — the sibling-worktree fallback tier."""
    _write_marker(tmp_path, "siblinghash.session-a", "deadbeef")
    result = _marker_value_present(tmp_path, "deadbeef", "currenthash.", "siblinghash.")
    assert result.returncode == 0


def test_marker_value_present_ignores_non_matching_prefix(tmp_path: Path) -> None:
    """The right value stored under the wrong repo-hash prefix must not release.

    This is the cross-repo boundary: dropping the repo key entirely would let a
    review performed against a different codebase authorize this one.
    """
    _write_marker(tmp_path, "otherrepohash.session-a", "deadbeef")
    assert _marker_value_present(tmp_path, "deadbeef", "repohash.").returncode != 0


def test_marker_value_present_requires_whole_line_match(tmp_path: Path) -> None:
    """A stored value that merely CONTAINS the expected value is not a match.

    Pins the `-x` (whole-line) flag: a regression to bare `-F` would let a
    longer digest sharing this one as a substring release the gate.
    """
    _write_marker(tmp_path, "repohash.session-a", "deadbeefextra")
    assert _marker_value_present(tmp_path, "deadbeef", "repohash.").returncode != 0


def test_marker_value_present_rejects_stored_value_shorter_than_expected(tmp_path: Path) -> None:
    """The inverse substring direction: stored value is a proper prefix of expected."""
    _write_marker(tmp_path, "repohash.session-a", "dead")
    assert _marker_value_present(tmp_path, "deadbeef", "repohash.").returncode != 0


def test_marker_value_present_zero_match_glob_reports_not_found(tmp_path: Path) -> None:
    """No marker for this prefix → clean not-found, not a grep error.

    Without `nullglob`, bash expands a zero-match `prefix*` to the literal
    unexpanded pattern string, which grep then fails to open. "No marker exists
    yet" is the most common call, so that failure mode would misreport the
    common case as an error. The empty-stderr assertion is what catches it.
    """
    result = _marker_value_present(tmp_path, "deadbeef", "nosuchrepohash.")
    assert result.returncode != 0
    assert result.stderr == "", repr(result.stderr)


def test_marker_value_present_absent_directory_reports_not_found(tmp_path: Path) -> None:
    """A markers directory that does not exist yet is not-found, not an error."""
    result = _marker_value_present(tmp_path / "never-created", "deadbeef", "repohash.")
    assert result.returncode != 0
    assert result.stderr == "", repr(result.stderr)


def test_marker_value_present_requires_a_prefix(tmp_path: Path) -> None:
    """With no prefixes supplied, the helper reports not-found rather than scanning all."""
    _write_marker(tmp_path, "repohash.session-a", "deadbeef")
    assert _marker_value_present(tmp_path, "deadbeef").returncode != 0


def test_marker_value_present_empty_expected_value_reports_not_found(tmp_path: Path) -> None:
    """An empty expected value never matches.

    A caller reaches this helper with an uncomputed hash only by skipping its
    own fail-closed check; matching an empty marker file there would convert a
    hashing failure into a gate release.
    """
    _write_marker(tmp_path, "repohash.session-a", "")
    assert _marker_value_present(tmp_path, "", "repohash.").returncode != 0


def test_marker_value_present_ignores_subdirectories(tmp_path: Path) -> None:
    """A stray subdirectory under the markers dir does not produce grep noise."""
    _write_marker(tmp_path, "repohash.session-a", "deadbeef")
    (tmp_path / "repohash.stray-subdir").mkdir()
    result = _marker_value_present(tmp_path, "deadbeef", "repohash.")
    assert result.returncode == 0
    assert result.stderr == "", repr(result.stderr)


def test_marker_value_present_restores_nullglob_state() -> None:
    """The helper must not leak `nullglob` into its caller's shell.

    Hooks source _lib.sh and then run their own globs; silently flipping a
    shell option under them would change unrelated behavior far from this
    call site.
    """
    harness = (
        f". {_LIB_SH}; "
        "_lib_marker_value_present /nonexistent-markers-dir val prefix. ; "
        "shopt -q nullglob && echo LEAKED || echo RESTORED"
    )
    result = subprocess.run(
        ["bash", "-c", harness], capture_output=True, text=True, check=False
    )
    assert result.stdout.strip() == "RESTORED", repr(result.stdout)


# --- _lib_is_no_gate_release_agent ---------------------------------------


def _is_no_gate_release_agent(agent_type: str) -> bool:
    result = subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_is_no_gate_release_agent "$1"', "bash", agent_type],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _no_gate_release_agents() -> list[str]:
    result = subprocess.run(
        ["bash", "-c", f". {_LIB_SH}; _lib_no_gate_release_agents"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def test_no_gate_release_set_covers_every_review_only_agent() -> None:
    """The set is a superset of the review-only roster, by derivation not by copy."""
    review_only = subprocess.run(
        ["bash", "-c", f". {_LIB_SH}; _lib_review_only_agents"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert review_only, "review-only roster must not be empty"
    assert set(review_only) <= set(_no_gate_release_agents())


def test_no_gate_release_set_includes_code_writer() -> None:
    """code-writer is an implementer, so it is absent from the review-only roster —
    but it is exactly the identity that can release a gate it could not have
    earned, so the no-release set must name it separately."""
    assert "code-writer" in _no_gate_release_agents()


@pytest.mark.parametrize(
    "agent_type", ["code-writer", "staff-sdet", "ciso-reviewer", "Explore", "Plan"]
)
def test_no_gate_release_agent_matches_roster_members(agent_type: str) -> None:
    assert _is_no_gate_release_agent(agent_type)


@pytest.mark.parametrize(
    "agent_type",
    ["general-purpose", "claude", "", "code-write", "code-writer-x", "CODE-WRITER"],
)
def test_no_gate_release_agent_rejects_non_members(agent_type: str) -> None:
    """Exact match only.

    Empty agent_type is the main session and must pass. general-purpose and
    claude carry the full tool set and can genuinely run a review skill, so
    they keep the documented delegation escape hatch. The near-miss strings
    pin that the predicate is not doing prefix or case-insensitive matching.
    """
    assert not _is_no_gate_release_agent(agent_type)


# --- _lib_valid_session_id_component --------------------------------------
#
# Every call site that builds a filesystem path from a hook-payload-supplied
# session id ("$STATE_DIR/$SESSION_ID") relies on this predicate to reject a
# value that would escape the intended directory once concatenated in.


def _valid_session_id_component(session_id: str) -> bool:
    result = subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_valid_session_id_component "$1"', "bash", session_id],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


@pytest.mark.parametrize(
    "session_id",
    [
        "550e8400-e29b-41d4-a716-446655440000",  # a real harness session id (UUID)
        "abc123",
        "session_ID-with-underscores",
    ],
)
def test_valid_session_id_component_accepts_uuid_shaped_ids(session_id: str) -> None:
    assert _valid_session_id_component(session_id)


@pytest.mark.parametrize(
    "session_id",
    [
        "",
        "../canary",
        "../../etc/passwd",
        "foo/bar",
        "/absolute/path",
        "session id with spaces",
        "session;rm -rf /",
        ".",
        "..",
    ],
)
def test_valid_session_id_component_rejects_path_escaping_ids(session_id: str) -> None:
    """Each of these, concatenated into "$DIR/$SESSION_ID", would either
    escape DIR (`../`, an absolute path) or otherwise fail to name a single
    safe path component."""
    assert not _valid_session_id_component(session_id)


# --- _lib_active_bypass_marker_live ---------------------------------------
#
# The four bypass-shaped gates (require-{memory-skill,plan-review,
# ready-for-review,respond-pr}.sh) share this helper, so the liveness,
# orphan-eviction, and traversal properties are pinned once here rather than
# four times over. Each hook's own test file still asserts the disposition its
# gate produces when the helper returns false, which is what differs by gate.

_MARKER_DIR_NAME = ".test-skill-active.d"


def _active_bypass_marker_live(home: Path, session_id: str) -> bool:
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'. {_LIB_SH}; _lib_active_bypass_marker_live "$1" "$2"',
            "bash",
            _MARKER_DIR_NAME,
            session_id,
        ],
        capture_output=True,
        text=True,
        env={"HOME": str(home), "PATH": os.environ["PATH"]},
        check=False,
    )
    return result.returncode == 0


def _write_active_bypass_marker(home: Path, session_id: str, content: str) -> Path:
    marker_dir = home / ".claude" / _MARKER_DIR_NAME
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker = marker_dir / session_id
    marker.write_text(content)
    return marker


def test_active_bypass_marker_live_true_for_live_pid(tmp_path) -> None:
    """The whole point of the marker: a running skill's PID means the gate
    should let that session's own calls through."""
    _write_active_bypass_marker(tmp_path, "sess-live", str(os.getpid()))
    assert _active_bypass_marker_live(tmp_path, "sess-live")


def test_active_bypass_marker_live_false_when_no_marker(tmp_path) -> None:
    assert not _active_bypass_marker_live(tmp_path, "sess-absent")


@pytest.mark.parametrize(
    "content",
    [
        "2147483647",  # above the default pid_max — no such process
        "",
        "not-a-pid",
        "-1",
    ],
    ids=["dead-pid", "empty", "non-numeric", "negative"],
)
def test_active_bypass_marker_live_evicts_orphan(tmp_path, content: str) -> None:
    """A marker whose PID is dead or unreadable is an orphan from a session
    that died before its cleanup step. It must not release the gate, and it
    must be removed so it cannot accumulate."""
    marker = _write_active_bypass_marker(tmp_path, "sess-orphan", content)
    assert not _active_bypass_marker_live(tmp_path, "sess-orphan")
    assert not marker.exists(), "an orphaned marker must be evicted, not left in place"


def test_active_bypass_marker_live_keeps_live_marker(tmp_path) -> None:
    """Eviction is for orphans only — a live marker must survive being read,
    or the first gate hit would revoke the running skill's own bypass."""
    marker = _write_active_bypass_marker(tmp_path, "sess-keep", str(os.getpid()))
    assert _active_bypass_marker_live(tmp_path, "sess-keep")
    assert marker.exists()


def test_active_bypass_marker_live_false_for_traversal_id_without_touching_target(
    tmp_path,
) -> None:
    """The guard is inside the helper, so a path-escaping id must be rejected
    before the marker path is built — even when a live-PID file sits exactly
    where the traversal would resolve. Planting the PID there is what
    discriminates: without the guard the helper would read it, find it live,
    and return true."""
    (tmp_path / ".claude" / _MARKER_DIR_NAME).mkdir(parents=True)
    canary = tmp_path / ".claude" / "canary"
    canary.write_text(str(os.getpid()))

    assert not _active_bypass_marker_live(tmp_path, "../canary")
    assert canary.exists(), "a traversal id must not let the eviction rm escape the marker dir"


# Empty session_id is deliberately NOT tested here. `$HOME/.claude/<dir>/` with
# an empty id names the marker directory itself, which `[ -f ]` rejects whether
# or not the guard ran — no canary placement can separate the two states, so any
# such test would pass with the guard deleted and pin nothing. Empty-id
# rejection is pinned where it is discriminating: the parametrized rejection
# table for _lib_valid_session_id_component above, which includes "".


# --- Universal adoption of the session-id guard ----------------------------


def test_every_hook_that_paths_a_session_id_validates_it() -> None:
    """Any hook that interpolates SESSION_ID into a filesystem path must also
    call the guard — either directly or via _lib_active_bypass_marker_live.

    This is a convention test, not a behavior test: it proves the call is
    written, not that it runs. Each hook's own traversal test pins the runtime
    behavior. This one exists because the failure it catches is a NEW hook
    added later that builds a marker path and forgets to validate — a file
    that has no traversal test yet by definition, so no behavioral test can
    cover it. Ten hooks currently qualify; the guard was applied to all of
    them as a class rather than to the one where the defect first surfaced.
    """
    hooks_dir = _LIB_SH.parent
    repo_root = hooks_dir.parents[3]
    hook_files = [
        path
        for path in sorted(hooks_dir.glob("*.sh")) + sorted(repo_root.glob("plugins/*/hooks/*.sh"))
        if path.name != "_lib.sh"
    ]
    assert hook_files, "no hook scripts found — the glob is wrong, not the repo"

    # Matches "<anything>/$SESSION_ID" and "<anything>/${SESSION_ID}", which is
    # the shape that turns an unvalidated id into a path outside the intended
    # directory. A bare $SESSION_ID (logged, compared, passed as an argument)
    # is not a path build and does not require the guard.
    builds_path_re = re.compile(r"/\$\{?SESSION_ID\b")
    guards = ("_lib_valid_session_id_component", "_lib_active_bypass_marker_live")

    unguarded = []
    matched_any = []
    for hook in hook_files:
        text = hook.read_text()
        if not builds_path_re.search(text):
            continue
        matched_any.append(hook.name)
        if not any(guard in text for guard in guards):
            unguarded.append(hook.name)

    assert matched_any, (
        "no hook matched the path-building pattern — the regex has drifted from "
        "the code and this test is now vacuous"
    )
    assert not unguarded, (
        "these hooks build a filesystem path from session_id without validating "
        f"it first: {unguarded}. Call _lib_valid_session_id_component, or route "
        "the marker read through _lib_active_bypass_marker_live."
    )


# --- _lib_first_live_linked_worktree --------------------------------------


def _first_live_linked_worktree(repo_root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_first_live_linked_worktree "$1"', "bash", str(repo_root)],
        capture_output=True,
        text=True,
        check=False,
    )


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "f.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


def test_first_live_linked_worktree_finds_a_present_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    wt = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "-b", "feature", str(wt)], cwd=repo, check=True, capture_output=True
    )

    result = _first_live_linked_worktree(repo)

    assert result.returncode == 0
    assert result.stdout == str(wt)


def test_first_live_linked_worktree_ignores_a_stale_unpruned_entry(tmp_path: Path) -> None:
    """`git worktree list` still reports an entry whose directory was deleted
    without `git worktree prune` or `git worktree remove`. That stale entry
    must not count as a live worktree."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    wt = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "-b", "feature", str(wt)], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(["rm", "-rf", str(wt)], check=True)

    result = _first_live_linked_worktree(repo)

    assert result.returncode != 0
    assert result.stdout == ""


def test_first_live_linked_worktree_returns_not_found_with_no_worktree_at_all(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)

    result = _first_live_linked_worktree(repo)

    assert result.returncode != 0
    assert result.stdout == ""


# --- _lib_fragment_command_word / _lib_fragment_invokes_tool /
#     _lib_fragment_has_token --------------------------------------------
#
# Promoted out of deny-reviewer-tree-mutation.sh into _lib.sh once
# deny-repo-relocation.sh needed the identical "does this fragment invoke
# tool X" check. Previously covered only indirectly through
# deny-reviewer-tree-mutation.sh's black-box tests, which stopped being
# sufficient once a second hook depends on the same shared functions.


def _fragment_command_word(fragment: str) -> str:
    result = subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_fragment_command_word "$1"', "bash", fragment],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _fragment_invokes_tool(fragment: str, tool: str) -> bool:
    result = subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_fragment_invokes_tool "$1" "$2"', "bash", fragment, tool],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _fragment_has_token(fragment: str, token: str) -> bool:
    result = subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_fragment_has_token "$1" "$2"', "bash", fragment, token],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


class TestFragmentCommandWord:
    def test_bare_command_returns_itself(self) -> None:
        assert _fragment_command_word("black src/x.py") == "black"

    def test_skips_leading_env_var_assignment(self) -> None:
        assert _fragment_command_word("FOO=1 black src/x.py") == "black"

    def test_skips_sudo_runner(self) -> None:
        assert _fragment_command_word("sudo black src/x.py") == "black"

    def test_skips_runner_and_connector_subtoken(self) -> None:
        assert _fragment_command_word("poetry run black src/x.py") == "black"

    def test_skips_npx_flag_before_command(self) -> None:
        assert _fragment_command_word("npx --yes prettier --write x.ts") == "prettier"

    def test_resolves_absolute_path_runner_and_command(self) -> None:
        assert _fragment_command_word("/usr/bin/python -m black src/x.py") == "black"

    def test_argument_word_is_not_mistaken_for_command_word(self) -> None:
        """The whole reason this is a command-word scan, not an any-word
        scan: a tool name appearing only as an argument must not resolve as
        the command."""
        assert _fragment_command_word("grep -rn black .") == "grep"

    def test_empty_fragment_returns_empty(self) -> None:
        assert _fragment_command_word("") == ""


class TestFragmentInvokesTool:
    def test_exact_match(self) -> None:
        assert _fragment_invokes_tool("mv /a /b", "mv")

    def test_path_qualified_match(self) -> None:
        assert _fragment_invokes_tool("/usr/bin/mv /a /b", "mv")

    def test_no_match_for_different_tool(self) -> None:
        assert not _fragment_invokes_tool("cp /a /b", "mv")

    def test_no_match_when_tool_name_is_only_an_argument(self) -> None:
        assert not _fragment_invokes_tool("grep mv notes.txt", "mv")

    def test_runner_wrapped_match(self) -> None:
        assert _fragment_invokes_tool("sudo env X=1 rsync -a /a /b", "rsync")


class TestFragmentHasToken:
    def test_standalone_token_matches(self) -> None:
        assert _fragment_has_token("rsync -a --remove-source-files /a /b", "--remove-source-files")

    def test_token_as_substring_of_longer_word_does_not_match(self) -> None:
        assert not _fragment_has_token("rsync --remove-source-files-extra /a /b", "--remove-source-files")

    def test_token_at_string_start_matches(self) -> None:
        assert _fragment_has_token("fmt x.tf", "fmt")

    def test_token_at_string_end_matches(self) -> None:
        assert _fragment_has_token("terraform fmt", "fmt")

    def test_missing_token_does_not_match(self) -> None:
        assert not _fragment_has_token("rsync -a /a /b", "--remove-source-files")


# --- _lib_realpath_m ---------------------------------------------------
#
# GNU `realpath -m` is available natively in this test environment, so a
# bare call exercises only the fast path (native -m succeeds immediately).
# The fallback path (walk to nearest existing ancestor + reattach) only
# runs when BOTH native `-m` and `grealpath` are unavailable — forced here
# via a PATH built from a fake `realpath` shim (errors on -m, delegates to
# the real system realpath otherwise) plus /usr/bin:/bin only, deliberately
# excluding /usr/local/bin, where this dev machine's Homebrew `grealpath`
# actually lives. Without this, `command -v grealpath` would still find it
# and the fallback branch under test would never run.

_FORCED_FALLBACK_REALPATH_SHIM = textwrap.dedent("""\
    #!/bin/bash
    if [ "$1" = "-m" ]; then
      echo "realpath: illegal option -- m" >&2
      exit 1
    fi
    exec /bin/realpath "$@"
""")


def _run_realpath_m(target: str, forced_fallback: bool = False, tmp_path: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if forced_fallback:
        assert tmp_path is not None, "forced_fallback requires tmp_path"
        shim_dir = tmp_path / "realpath_shim"
        shim_dir.mkdir(exist_ok=True)
        shim = shim_dir / "realpath"
        shim.write_text(_FORCED_FALLBACK_REALPATH_SHIM)
        shim.chmod(0o755)
        env["PATH"] = f"{shim_dir}:/usr/bin:/bin"
    script = f'set -uo pipefail; . {_LIB_SH}; _lib_realpath_m "$1"'
    return subprocess.run(
        ["bash", "-c", script, "bash", target],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


class TestLibRealpathM:
    def test_native_fast_path_resolves_existing_dir(self) -> None:
        result = _run_realpath_m("/tmp")
        assert result.returncode == 0
        assert result.stdout.strip() in ("/tmp", "/private/tmp")

    def test_forced_fallback_resolves_nonexistent_leaf(self, tmp_path: Path) -> None:
        target = tmp_path / "does-not-exist-xyz123.txt"
        result = _run_realpath_m(str(target), forced_fallback=True, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(target)

    def test_forced_fallback_resolves_multi_level_nonexistent_path(self, tmp_path: Path) -> None:
        target = tmp_path / "newdir1" / "newdir2" / "newfile.txt"
        result = _run_realpath_m(str(target), forced_fallback=True, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(target)

    def test_forced_fallback_rejects_dotdot_in_unresolved_suffix(self, tmp_path: Path) -> None:
        """Required regression test for a High-severity finding: the
        fallback's not-yet-existing suffix must not be reattached verbatim
        when it contains a `..` component, or a boundary check comparing
        the result against a same-prefix glob (require-plan-review.sh's
        agent-reviews/ exemption, its repo-boundary check, and
        require-memory-skill.sh's memory-tree classification) can be
        satisfied by a path that is semantically outside the intended
        boundary. Fails closed (empty output, exit 1) instead."""
        target = tmp_path / "agent-reviews" / "newdir" / ".." / ".." / ".." / "etc" / "passwd_pwned"
        result = _run_realpath_m(str(target), forced_fallback=True, tmp_path=tmp_path)
        assert result.returncode != 0 or not result.stdout.strip(), (
            f"expected fail-closed (empty/nonzero) for a `..`-bearing unresolved suffix, "
            f"got stdout={result.stdout!r} rc={result.returncode}"
        )

    def test_forced_fallback_no_double_slash_when_ancestor_is_root(self, tmp_path: Path) -> None:
        """Required regression test: when the nearest existing ancestor is
        `/` itself, the reattached suffix must not produce a doubled
        leading slash (`//foo` instead of `/foo`), which could desync from
        a canonically-formed comparison path in a caller's boundary check."""
        target = "/zzz-totally-nonexistent-root-for-test/x/y/z"
        result = _run_realpath_m(target, forced_fallback=True, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        resolved = result.stdout.strip()
        assert resolved == target
        assert "//" not in resolved


# _lib_autonomous_shipping_active — direct unit coverage.
#
# _lib_worktree_enforcement_active has no such coverage anywhere in this
# suite; only its callers' integration tests guard it. This function does
# not inherit that gap — see
# test_inactive_when_repo_commits_required_file_but_machine_file_absent
# below for the property that most needs pinning.


def _autonomous_shipping_active(home: Path, repo_root: Path | None, *extra_args: str) -> bool:
    args = [str(repo_root)] if repo_root is not None else []
    args.extend(extra_args)
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'. {_LIB_SH}; _lib_autonomous_shipping_active "$@"',
            "bash",
            *args,
        ],
        capture_output=True,
        text=True,
        env={"HOME": str(home), "PATH": os.environ["PATH"]},
        check=False,
    )
    return result.returncode == 0


class TestAutonomousShippingActive:
    def test_inactive_when_machine_file_absent(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        repo = tmp_path / "repo"
        (home / ".claude").mkdir(parents=True)
        repo.mkdir()
        assert not _autonomous_shipping_active(home, repo)

    def test_active_when_machine_file_present(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        repo = tmp_path / "repo"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "autonomous-shipping-required").touch()
        repo.mkdir()
        assert _autonomous_shipping_active(home, repo)

    def test_inactive_when_machine_file_present_and_repo_optout(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        repo = tmp_path / "repo"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "autonomous-shipping-required").touch()
        (repo / ".claude").mkdir(parents=True)
        (repo / ".claude" / "autonomous-shipping-optout").touch()
        assert not _autonomous_shipping_active(home, repo)

    def test_inactive_when_repo_commits_required_file_but_machine_file_absent(
        self, tmp_path: Path
    ) -> None:
        """The central guarantee this function exists to provide: a repo's
        own committed .claude/autonomous-shipping-required must never grant
        anything by itself. Only the engineer's own machine state can."""
        home = tmp_path / "home"
        repo = tmp_path / "repo"
        (home / ".claude").mkdir(parents=True)
        (repo / ".claude").mkdir(parents=True)
        (repo / ".claude" / "autonomous-shipping-required").touch()
        assert not _autonomous_shipping_active(home, repo)

    def test_inactive_when_repo_root_empty(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "autonomous-shipping-required").touch()
        assert not _autonomous_shipping_active(home, None, "")

    def test_inactive_when_home_empty(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        result = subprocess.run(
            [
                "bash",
                "-c",
                f'. {_LIB_SH}; _lib_autonomous_shipping_active "$1"',
                "bash",
                str(repo),
            ],
            capture_output=True,
            text=True,
            env={"HOME": "", "PATH": os.environ["PATH"]},
            check=False,
        )
        assert result.returncode != 0

    def test_inactive_when_home_is_bare_root(self, tmp_path: Path) -> None:
        """Pins the ${HOME%/} normalization specifically: HOME=/ strips to
        an empty home_norm, so the [ -n "$home_norm" ] guard fires before any
        filesystem probe — deterministically, not because /.claude/
        autonomous-shipping-required happens to be absent on this machine.
        Losing the %/ strip would make this depend on ambient root-filesystem
        state instead."""
        repo = tmp_path / "repo"
        repo.mkdir()
        result = subprocess.run(
            [
                "bash",
                "-c",
                f'. {_LIB_SH}; _lib_autonomous_shipping_active "$1"',
                "bash",
                str(repo),
            ],
            capture_output=True,
            text=True,
            env={"HOME": "/", "PATH": os.environ["PATH"]},
            check=False,
        )
        assert result.returncode != 0

    def test_inactive_on_wrong_arity(self, tmp_path: Path) -> None:
        """Extra positional (not zero args) so $1 stays bound under set -u —
        a zero-arg call can't distinguish a controlled `return 1` from an
        interpreter abort on the unbound $1 dereference, since both exit
        nonzero. This call isolates the [ "$#" -eq 1 ] guard itself."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "autonomous-shipping-required").touch()
        result = subprocess.run(
            [
                "bash",
                "-c",
                f'set -u; . {_LIB_SH}; _lib_autonomous_shipping_active "$1" "$2"',
                "bash",
                str(tmp_path / "repo"),
                "unexpected-extra-arg",
            ],
            capture_output=True,
            text=True,
            env={"HOME": str(home), "PATH": os.environ["PATH"]},
            check=False,
        )
        assert result.returncode != 0
        assert "unbound variable" not in result.stderr


# --- Shared credential-guard constants -------------------------------------
#
# Per-hook behavior against these constants is exercised end to end by
# test_deny_credential_bash_reads.py, test_deny_credential_file_reads.py,
# test_redact_credential_values.py, and the credential-value cases in
# test_deny_pii_in_commits.py. The tests here pin only that the constants
# exist, are sourceable, and hold the specific values every consuming hook
# relies on — a single source of truth that drifted silently would still
# pass each consumer's own tests if a hook simply hardcoded a copy instead.


def _sourced_value(var_name: str) -> str:
    result = subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; printf "%s" "${var_name}"'],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def test_lib_size_threshold_bytes_is_five_megabytes() -> None:
    """5 MB, promoted from deny-data-file-reads.sh's original literal.
    redact-credential-values.sh's size cap reuses this same value."""
    assert _sourced_value("_LIB_SIZE_THRESHOLD_BYTES") == "5242880"


def test_lib_credential_path_regex_compiles_and_matches_ssh_key() -> None:
    result = subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; printf "%s" "cat ~/.ssh/id_rsa" | grep -qE "$_LIB_CREDENTIAL_PATH_REGEX"'],
        check=False,
    )
    assert result.returncode == 0


def test_lib_credential_path_regex_excludes_pub_key() -> None:
    result = subprocess.run(
        [
            "bash", "-c",
            f'. {_LIB_SH}; printf "%s" "cat ~/.ssh/id_rsa.pub" | grep -qE "$_LIB_CREDENTIAL_PATH_REGEX"',
        ],
        check=False,
    )
    assert result.returncode != 0


def test_lib_credential_value_regex_compiles_under_grep_and_jq() -> None:
    """Must compile under both engines it's shared between: grep -E
    (deny-pii-in-commits.sh) and jq's gsub (redact-credential-values.sh)."""
    token = "ghp_abcdefghijklmnopqrstuvwx1234"
    grep_harness = f'. {_LIB_SH}; printf "%s" "{token}" | grep -qE "$_LIB_CREDENTIAL_VALUE_REGEX"'
    grep_result = subprocess.run(["bash", "-c", grep_harness], check=False)
    assert grep_result.returncode == 0

    jq_harness = (
        f'. {_LIB_SH}; jq -n --arg pattern "$_LIB_CREDENTIAL_VALUE_REGEX" --arg s "{token}" '
        "'$s | test($pattern)'"
    )
    jq_result = subprocess.run(["bash", "-c", jq_harness], capture_output=True, text=True, check=True)
    assert jq_result.stdout.strip() == "true"


def test_lib_pem_private_key_block_regex_matches_full_block_under_jq() -> None:
    """Redaction-only counterpart to _LIB_CREDENTIAL_VALUE_REGEX's header-only
    PEM alternative — must compile under jq's Oniguruma engine (its only
    consumer, redact-credential-values.sh) and match a full synthetic
    header-through-footer block, not only the header line."""
    pem_block = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpAIBAAKCAQEAsecretkeybodyherethatisverylongandsecret\n"
        "-----END RSA PRIVATE KEY-----"
    )
    jq_harness = (
        f'. {_LIB_SH}; jq -n --arg pattern "$_LIB_PEM_PRIVATE_KEY_BLOCK_REGEX" --arg s "{pem_block}" '
        "'$s | test($pattern)'"
    )
    jq_result = subprocess.run(["bash", "-c", jq_harness], capture_output=True, text=True, check=True)
    assert jq_result.stdout.strip() == "true"
