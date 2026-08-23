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


def test_success_appends_log_line_and_removes_ignored_marker(tmp_path: Path) -> None:
    """Both side effects of a resolvable session, asserted explicitly in the
    same case: the log line is appended and this session's escalation-ladder
    marker is removed."""
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
    assert not marker.exists()


def test_path_escaping_session_id_exits_zero_without_touching_paths_outside_marker_dir(
    tmp_path: Path,
) -> None:
    """A session id containing '..' or '/' would escape
    .handoff-nudge-fired.d/ once concatenated into the rm -f path -- the same
    risk marker.sh's own _resolve_session_id chokepoint guards against.
    Rejecting it before either side effect runs means neither the log gets a
    line for it nor rm -f is ever invoked with the escaping path."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    _seed_session(config_dir, "../evil")
    canary = config_dir / "evil-ignored"
    canary.write_text("do not touch")

    result = _run(config_dir)

    assert result.returncode == 0
    assert not (config_dir / ".handoff-nudge.log").exists()
    assert canary.read_text() == "do not touch"


def test_stray_argument_is_ignored_and_success_path_still_runs(tmp_path: Path) -> None:
    """The script takes no arguments; a stray one is silently ignored rather
    than crashing or changing behavior."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    _seed_session(config_dir, "test-session-xyz")

    result = _run(config_dir, ["unexpected-arg"])

    assert result.returncode == 0
    assert (config_dir / ".handoff-nudge.log").read_text() == "handoff session=test-session-xyz\n"
