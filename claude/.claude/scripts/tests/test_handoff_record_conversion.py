"""Tests for handoff-record-conversion.sh.

Session-id resolution runs through _lib_resolve_claude_pid (hooks/_lib.sh),
which walks process ancestors looking for a sessions/<pid> file written in
capture-session-id.sh's two-line shape (session id, then that pid's
`TZ=UTC LC_ALL=C ps -o lstart=` start time). _seed_session below builds that
fixture the same way hooks/tests/conftest.py's helper of the same name does
-- not importable directly, since hooks/tests and scripts/tests are separate
pytest rootdirs.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

_SCRIPT = Path(__file__).parent.parent / "handoff-record-conversion.sh"


def _seed_session(config_dir: Path, session_id: str, pid: int | None = None) -> None:
    """Write <config_dir>/sessions/<pid> in the two-line format
    capture-session-id.sh writes, so _lib_resolve_claude_pid's ancestor walk
    resolves session_id for pid. pid defaults to this test process's own
    pid: subprocess.run spawns the script as a direct child of the current
    pytest process, so the script's own $PPID lands here directly."""
    target_pid = os.getpid() if pid is None else pid
    sessions_dir = config_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    start_time = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(target_pid)],
        env={**os.environ, "TZ": "UTC", "LC_ALL": "C"},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.rstrip("\n")
    (sessions_dir / str(target_pid)).write_text(f"{session_id}\n{start_time}\n")


def _run(config_dir: Path, args: list[str] | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    return subprocess.run(
        [str(_SCRIPT), *(args or [])],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_no_resolvable_session_exits_zero_with_no_side_effects(tmp_path: Path) -> None:
    """No ancestor carries a sessions/<pid> file: the ancestor walk in
    _lib_resolve_claude_pid exhausts and returns non-zero, so the script
    exits 0 silently without writing the log or touching any marker."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    result = _run(config_dir)

    assert result.returncode == 0
    assert result.stdout == ""
    assert not (config_dir / ".handoff-nudge.log").exists()
    assert not (config_dir / ".handoff-nudge-fired.d").exists()


def test_success_appends_log_line_and_leaves_ignored_marker_alone(tmp_path: Path) -> None:
    """The log line is appended. The escalation-ladder marker is left alone:
    the hard block is gated on an absolute token position, independent of
    the ignored-re-arm count, so nothing in this script's success path
    resets it."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    _seed_session(config_dir, "test-session-abc")
    marker_dir = config_dir / ".handoff-nudge-fired.d"
    marker_dir.mkdir()
    marker = marker_dir / "test-session-abc-ignored"
    marker.write_text("")

    result = _run(config_dir)

    assert result.returncode == 0
    assert (config_dir / ".handoff-nudge.log").read_text() == "handoff session=test-session-abc\n"
    assert marker.exists()


def test_path_escaping_session_id_exits_zero_without_writing_log(
    tmp_path: Path,
) -> None:
    """A session id containing '..' or '/' falls outside the guard's
    conservative [A-Za-z0-9_-] allow-list -- the same allow-list that also
    blocks whitespace from corrupting the log line's key=value tokenization.
    Rejecting it before the log write means no line is appended for it."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    _seed_session(config_dir, "../evil")

    result = _run(config_dir)

    assert result.returncode == 0
    assert not (config_dir / ".handoff-nudge.log").exists()


def test_stray_argument_is_ignored_and_success_path_still_runs(tmp_path: Path) -> None:
    """The script takes no arguments; a stray one is silently ignored rather
    than crashing or changing behavior."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    _seed_session(config_dir, "test-session-xyz")

    result = _run(config_dir, ["unexpected-arg"])

    assert result.returncode == 0
    assert (config_dir / ".handoff-nudge.log").read_text() == "handoff session=test-session-xyz\n"
