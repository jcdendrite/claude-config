"""Unit tests for _lib.sh's _lib_hook_claude_pid and _lib_valid_session_id_component,
and for each hook's session-id path validation.
"""
from __future__ import annotations

import os
import re
import subprocess

import pytest

from ._lib_sh_helpers import _LIB_SH, _run_lib_call
from .test_config_lib import _locale_that_widens_ascii_ranges


def test_lib_hook_claude_pid_equal_to_ppid_is_accepted() -> None:
    """`_run_lib_call` spawns bash directly (no intervening shell), so that
    bash process's own $PPID is this pytest process's pid -- matching
    $CLAUDE_PID exactly, the no-shim disjunct."""
    env = {**os.environ, "CLAUDE_PID": str(os.getpid())}
    result = _run_lib_call("_lib_hook_claude_pid", env)
    assert result.returncode == 0, repr(result)
    assert result.stdout.strip() == str(os.getpid())


def test_lib_hook_claude_pid_equal_to_ppids_parent_is_accepted() -> None:
    """The shim disjunct: $CLAUDE_PID equals $PPID's immediate parent, not
    $PPID itself. An intervening `sh` forks the bash process that sources
    _lib.sh, so that bash's own $PPID is the shim's (sh's) pid, and
    $CLAUDE_PID (this test's own pid) is that shim's parent."""
    env = {**os.environ, "CLAUDE_PID": str(os.getpid())}
    result = subprocess.run(
        ["sh", "-c", f'bash -c ". {_LIB_SH}; _lib_hook_claude_pid"'],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0, repr(result)
    assert result.stdout.strip() == str(os.getpid())


def test_lib_hook_claude_pid_rejects_unrelated_live_pid() -> None:
    """A numeric, live $CLAUDE_PID outside the one-hop bound (not $PPID, not
    $PPID's immediate parent) must be rejected, falling back to $PPID -- the
    sole invariant the one-hop design exists to enforce. A `sleep` child of
    pytest is live but not an ancestor of the harness process at all."""
    sleeper = subprocess.Popen(["sleep", "30"])
    try:
        env = {**os.environ, "CLAUDE_PID": str(sleeper.pid)}
        result = _run_lib_call("_lib_hook_claude_pid", env)
        assert result.returncode == 0, repr(result)
        assert result.stdout.strip() == str(os.getpid())
    finally:
        sleeper.terminate()
        sleeper.wait()


def test_lib_hook_claude_pid_rejects_non_numeric_value() -> None:
    """A non-numeric $CLAUDE_PID must fall back to $PPID rather than being
    compared against it or used as-is."""
    env = {**os.environ, "CLAUDE_PID": "abc"}
    result = _run_lib_call("_lib_hook_claude_pid", env)
    assert result.returncode == 0, repr(result)
    assert result.stdout.strip() == str(os.getpid())


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


def test_valid_session_id_component_rejects_a_non_ascii_letter_under_a_widening_locale() -> None:
    """Under a locale whose collation puts accented letters inside `[A-Za-z]`,
    a bracket range accepts `sess<e-acute>`. The predicate spells its allowed
    characters out, so a revert to a range fails here on any runner that has
    such a locale."""
    result = subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_valid_session_id_component "$1"', "bash", "sess\u00e9"],
        env={**os.environ, "LC_ALL": _locale_that_widens_ascii_ranges()},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0


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
        "abc\n",  # trailing newline: `$` must not stop matching before it
        "\nabc",
        "ab\ncd",
    ],
)
def test_valid_session_id_component_rejects_path_escaping_ids(session_id: str) -> None:
    """Each of these, concatenated into "$DIR/$SESSION_ID", would either
    escape DIR (`../`, an absolute path), carry a newline, or otherwise fail
    to name a single safe path component."""
    assert not _valid_session_id_component(session_id)


# --- Universal adoption of the session-id guard ----------------------------


def test_every_hook_that_paths_a_session_id_validates_it() -> None:
    """Any hook that interpolates SESSION_ID into a filesystem path must also
    call the guard — either directly or via _lib_active_bypass_marker_live.

    This is a convention test, not a behavior test: it proves the call is
    written, not that it runs. Each hook's own traversal test pins the runtime
    behavior. This one exists because the failure it catches is a NEW hook
    added later that builds a marker path and forgets to validate — a file
    that has no traversal test yet by definition, so no behavioral test can
    cover it. Eight hooks currently qualify; the guard was applied to all of
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
