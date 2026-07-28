"""Unit tests for _lib.sh helpers.

Covers _lib_parse_tool_input_or_deny and _lib_jq, plus the marker-read helper
_lib_marker_value_present and the gate-release agent predicate
_lib_is_no_gate_release_agent.

The parse tests drive the helper via a throwaway shell harness that defines
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
