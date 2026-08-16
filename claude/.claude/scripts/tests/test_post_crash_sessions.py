"""Tests for post-crash-sessions.py.

Every test that reaches build_report()/main() passes an explicit find_root
(or sets POST_CRASH_SESSIONS_FIND_ROOT) pointed at a tmp_path — the bounded
`find` sweep (source B) never walks the real $HOME. CLAUDE_CONFIG_DIR is
likewise pinned to an isolated tmp dir for every test in this suite (see
conftest.py's autouse fixture), so no test here reads real config-dir state.
"""
from __future__ import annotations

import importlib.util
import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import _dead_pid

_SCRIPT = Path(__file__).parent.parent / "post-crash-sessions.py"
_spec = importlib.util.spec_from_file_location("post_crash_sessions", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)

_LIB_SH = Path(__file__).parent.parent.parent / "hooks" / "_lib.sh"

_STRUCTURAL_DETECTOR_VARS = (
    "_LIB_IPV4_LITERAL_REGEX",
    "_LIB_SSH_KEY_PATH_REFERENCE_REGEX",
    "_LIB_HOME_ROOTED_PATH_REGEX",
    "_LIB_LONG_HEX_IDENTIFIER_REGEX",
    "_LIB_INTERNAL_HOSTNAME_REGEX",
    "_LIB_SLACK_CHANNEL_SHAPE_REGEX",
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _write_json(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def _registry_entry_json(**overrides) -> dict:
    data = {
        "sessionId": "sess-aaa",
        "pid": 4242,
        "procStart": "Mon Jan  1 00:00:00 2024",
        "cwd": "/tmp/example-project",
        "status": "idle",
        "startedAt": 1704067200000,
        "updatedAt": 1704067200000,
        "version": "2.1.221",
    }
    data.update(overrides)
    return data


def _write_registry_entry(sessions_dir: Path, pid: int, **overrides) -> Path:
    overrides.setdefault("pid", pid)
    return _write_json(sessions_dir / f"{pid}.json", _registry_entry_json(**overrides))


def _lock_json(**overrides) -> dict:
    data = {
        "sessionId": "sess-bbb",
        "pid": 5252,
        "procStart": "Mon Jan  1 00:00:00 2024",
        "acquiredAt": 1704067200000,
    }
    data.update(overrides)
    return data


def _write_lock(path: Path, **overrides) -> Path:
    return _write_json(path, _lock_json(**overrides))


def _write_transcript(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


def _meta_record(session_id: str) -> dict:
    return {"mode": "default", "sessionId": session_id, "type": "meta"}


def _cwd_record(cwd: str, *, branch: str = "main", ts: str = "2024-01-01T00:00:00Z", session_id: str = "x") -> dict:
    return {
        "type": "user", "cwd": cwd, "gitBranch": branch, "timestamp": ts, "sessionId": session_id,
        "message": {"role": "user", "content": "the user's actual first prompt — never printed by this tool"},
    }


def _fake_ps_lstart(alive: dict[int, str]):
    """Stub matching _ps_lstart's single-positional-pid call convention."""
    def _fn(pid):
        return alive.get(pid)
    return _fn


def _registry_entry(
    *, session_id: str = "s1", pid: int = 100, proc_start: str | None = "Mon Jan  1 00:00:00 2024",
    cwd: str | None = "/tmp/proj", mtime: float = 1000.0, version: str | None = "2.1.221",
    pid_mismatch: bool = False, updated_at: float | None = None, status: str | None = "idle",
    started_at: float | None = None, path: Path | None = None,
) -> _mod.RegistryEntry:
    return _mod.RegistryEntry(
        session_id=session_id, pid=pid, proc_start=proc_start, cwd=cwd, status=status,
        started_at=started_at, updated_at=updated_at, version=version, mtime=mtime,
        path=path or Path(f"/fake/sessions/{pid}.json"), pid_mismatch=pid_mismatch,
    )


def _lock_entry(
    *, session_id: str = "s2", pid: int = 200, proc_start: str | None = "Mon Jan  1 00:00:00 2024",
    acquired_at: float | None = None, mtime: float = 1000.0, path: Path | None = None,
) -> _mod.LockEntry:
    return _mod.LockEntry(
        session_id=session_id, pid=pid, proc_start=proc_start, acquired_at=acquired_at,
        mtime=mtime, path=path or Path("/fake/.claude/scheduled_tasks.lock"),
    )


def _transcript_info(
    *, session_id: str = "s1", cwd: str | None = "/tmp/proj", git_branch: str | None = "main",
    first_seen_ts: float | None = None, last_activity: float = 1000.0, has_main: bool = True,
    subagent_count: int = 0, path: Path | None = None,
) -> _mod.TranscriptInfo:
    return _mod.TranscriptInfo(
        session_id=session_id, cwd=cwd, git_branch=git_branch, first_seen_ts=first_seen_ts,
        last_activity=last_activity, has_main=has_main, subagent_count=subagent_count,
        path=path or Path("/fake/transcript.jsonl"),
    )


def _structural_detector_patterns() -> dict[str, str]:
    script = f"source '{_LIB_SH}'; " + "".join(f'printf "%s\\0" "${{{v}}}"; ' for v in _STRUCTURAL_DETECTOR_VARS)
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    values = result.stdout.split("\0")[:-1]
    return dict(zip(_STRUCTURAL_DETECTOR_VARS, values, strict=True))


def _blank_report(**overrides) -> _mod.Report:
    defaults = dict(
        rows=[], boot_time=1000.0, ps_usable=True, unparsed_registry=0, unparsed_lock=0,
        legacy_bare_pid_dead=[], find_timed_out=False, find_elapsed_seconds=0.1,
        version_drift=[], pid_mismatches=[], config_dirs=[Path("/fake/config")],
        any_sessions_dir_found=True,
    )
    defaults.update(overrides)
    return _mod.Report(**defaults)


# ---------------------------------------------------------------------------
# _ms_to_seconds / _coerce_pid — field-allowlist scalar helpers
# ---------------------------------------------------------------------------

def test_ms_to_seconds_accepts_plausible_ms_epoch():
    assert _mod._ms_to_seconds(1700000000000) == 1700000000.0


def test_ms_to_seconds_rejects_value_that_looks_like_seconds_not_ms():
    assert _mod._ms_to_seconds(1700000000) is None


def test_ms_to_seconds_rejects_non_numeric():
    assert _mod._ms_to_seconds("not-a-number") is None


def test_ms_to_seconds_rejects_bool():
    """bool is an int subclass in Python; excluded so a stray JSON true/false never masquerades as a timestamp."""
    assert _mod._ms_to_seconds(True) is None


def test_coerce_pid_accepts_int():
    assert _mod._coerce_pid(100) == 100


def test_coerce_pid_accepts_numeric_string():
    assert _mod._coerce_pid("100") == 100


def test_coerce_pid_rejects_non_numeric_string():
    assert _mod._coerce_pid("abc") is None


def test_coerce_pid_rejects_bool():
    assert _mod._coerce_pid(True) is None


def test_coerce_pid_rejects_none():
    assert _mod._coerce_pid(None) is None


def test_safe_mtime_returns_value_for_existing_file(tmp_path):
    path = tmp_path / "f.txt"
    path.write_text("x")
    assert _mod._safe_mtime(path) == path.stat().st_mtime


def test_safe_mtime_returns_none_for_missing_path(tmp_path):
    """A stat() failure must degrade to None (unknown), never a substitute
    timestamp — a caller comparing it against boot_time could otherwise
    misclassify an unknown-age entry as pre-boot evidence."""
    assert _mod._safe_mtime(tmp_path / "does-not-exist") is None


def test_max_optional_float_both_known_returns_the_larger():
    assert _mod._max_optional_float(100.0, 200.0) == 200.0


def test_max_optional_float_one_none_returns_the_known_value():
    assert _mod._max_optional_float(None, 200.0) == 200.0
    assert _mod._max_optional_float(100.0, None) == 100.0


def test_max_optional_float_both_none_returns_none():
    assert _mod._max_optional_float(None, None) is None


def test_fmt_age_minutes_hours_days_mid_range():
    assert _mod._fmt_age(45 * 60) == "45m old"
    assert _mod._fmt_age(3 * 3600) == "3h old"
    assert _mod._fmt_age(12 * 86400) == "12d old"


def test_fmt_age_zero_floor():
    assert _mod._fmt_age(0.0) == "0m old"


def test_fmt_age_minute_to_hour_rollover_boundary():
    assert _mod._fmt_age(3599.999) == "59m old"
    assert _mod._fmt_age(3600.0) == "1h old"
    assert _mod._fmt_age(3601.0) == "1h old"


def test_fmt_age_hour_to_day_rollover_boundary():
    assert _mod._fmt_age(86399.999) == "23h old"
    assert _mod._fmt_age(86400.0) == "1d old"
    assert _mod._fmt_age(86401.0) == "1d old"


def test_sanitize_for_terminal_strips_control_and_escape_bytes():
    """An unstripped ESC byte in a git branch name or cwd could inject a
    terminal escape sequence (OSC title-set, clipboard write, output
    hiding) at render time."""
    hostile = "feature\x1b]0;pwned\x07-branch"
    assert _mod._sanitize_for_terminal(hostile) == "feature]0;pwned-branch"


def test_sanitize_for_terminal_preserves_ordinary_text():
    assert _mod._sanitize_for_terminal("feature/normal-branch") == "feature/normal-branch"


def test_sanitize_for_terminal_non_string_degrades_to_none_not_crash():
    """Schema drift: a field of the wrong JSON type (int, list, dict) must
    degrade rather than raising when this helper iterates its input."""
    assert _mod._sanitize_for_terminal(12345) is None
    assert _mod._sanitize_for_terminal(["a", "b"]) is None
    assert _mod._sanitize_for_terminal(None) is None


def test_render_report_resume_command_shell_quotes_a_hostile_cwd():
    """The resumable-row command is meant to be copy-pasted straight into a
    shell — a cwd containing shell metacharacters must come out quoted as
    an inert argument, not as an injectable second command."""
    row = _mod.SessionRow(
        session_id="s1", classification=_mod.CLASS_RESUMABLE,
        cwd="/tmp/evil; rm -rf ~", git_branch="main", last_activity=1000.0,
        detail="test detail", entry_count=1, cwd_missing=False,
    )
    output = _mod.render_report(_blank_report(rows=[row]), redact=False)
    assert "cd '/tmp/evil; rm -rf ~' && claude --resume s1" in output


# ---------------------------------------------------------------------------
# Boot-time seam: both platform branches, each independently faked
# ---------------------------------------------------------------------------

def test_boot_time_darwin_parses_sysctl_kern_boottime_output():
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 0, stdout="kern.boottime: { sec = 1700000000, usec = 123456 } Tue Nov 14 22:13:20 2023\n", stderr="",
        )
    assert _mod._boot_time_darwin(run=fake_run) == 1700000000.0


def test_boot_time_darwin_returns_none_on_nonzero_exit():
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="sysctl: unknown oid")
    assert _mod._boot_time_darwin(run=fake_run) is None


@pytest.mark.skipif(platform.system() != "Darwin", reason="kern.boottime is Darwin/BSD-only")
def test_boot_time_darwin_real_subprocess_returns_plausible_epoch():
    """Contract test against the actual sysctl binary, no injected run= —
    matching the existing real-subprocess precedent for _ps_lstart
    (test_ps_lstart_dead_pid_returns_none, test_ps_usable_true_for_real_self_pid)."""
    result = _mod._boot_time_darwin()
    assert result is not None
    assert 0 < result < time.time()


def test_boot_time_linux_parses_proc_stat_btime_line(tmp_path):
    stat_path = tmp_path / "stat"
    stat_path.write_text("cpu  0 0 0 0 0 0 0 0 0 0\nbtime 1700000000\nprocesses 100\n")
    assert _mod._boot_time_linux(stat_path) == 1700000000.0


def test_boot_time_linux_returns_none_when_proc_stat_missing(tmp_path):
    assert _mod._boot_time_linux(tmp_path / "does-not-exist") is None


def test_boot_time_dispatches_darwin_branch_regardless_of_host_platform():
    """Forces the Darwin branch with a faked source, independent of the platform this test actually runs on."""
    assert _mod._boot_time(system="Darwin", darwin_fn=lambda: 111.0) == 111.0


def test_boot_time_dispatches_linux_branch_regardless_of_host_platform():
    """Forces the Linux branch with a faked source, independent of the platform this test actually runs on."""
    assert _mod._boot_time(system="Linux", linux_fn=lambda: 222.0) == 222.0


def test_boot_time_unknown_platform_returns_none():
    assert _mod._boot_time(system="FreeBSD") is None


# ---------------------------------------------------------------------------
# _ps_lstart / _ps_usable / _same_process / _entry_liveness
# ---------------------------------------------------------------------------

def test_ps_lstart_dead_pid_returns_none():
    assert _mod._ps_lstart(_dead_pid()) is None


def test_ps_lstart_batch_empty_list_returns_empty_dict_no_subprocess_call():
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        raise AssertionError("should not be called for an empty pid list")

    assert _mod._ps_lstart_batch([], run=fake_run) == {}
    assert calls == []


def test_ps_lstart_batch_returns_lstart_for_self_and_omits_dead_pid():
    """One batched call resolves a mix of an alive pid (our own) and a dead
    one — the dead pid is simply absent, matching _ps_lstart's None."""
    dead = _dead_pid()
    alive = os.getpid()
    single = _mod._ps_lstart(alive)
    assert single is not None
    batch = _mod._ps_lstart_batch([alive, dead])
    assert batch.get(alive) == single
    assert dead not in batch


def test_ps_lstart_batch_matches_single_pid_call_for_the_same_process():
    """The batched -o pid=,lstart= format must parse to the same value the
    single-pid -o lstart= call returns for the same process."""
    pid = os.getpid()
    assert _mod._ps_lstart_batch([pid]).get(pid) == _mod._ps_lstart(pid)


def test_ps_lstart_ignores_hostile_ambient_timezone(monkeypatch):
    """A non-UTC TZ set in the ambient environment must not change the
    result — _ps_lstart pins TZ=UTC on its own subprocess call. Comparing
    against a baseline call (not a hardcoded expected string) means this
    only passes if the override is actually effective, not because the test
    runner happens to already be UTC. Uses a POSIX fixed-offset TZ string
    (parsed directly by libc's tzset, no zoneinfo database lookup) rather
    than a named zone, so this stays hostile on a minimal image with no
    tzdata package — verified against both Darwin and a bare ubuntu:24.04
    container. The LC_ALL/LC_TIME half of a locale+timezone hostile
    injection is intentionally not exercised here: on a bare ubuntu:24.04
    container (the base image CI's tests.yml pins), ps's lstart month name
    has no fr_FR.UTF-8 locale package to render against and silently falls
    back to English, so asserting on it there would pass even with the
    LC_ALL=C pinning removed."""
    pid = os.getpid()
    baseline = _mod._ps_lstart(pid)
    assert baseline is not None
    monkeypatch.setenv("TZ", "<+05>-5")
    hostile = _mod._ps_lstart(pid)
    assert hostile == baseline


def test_ps_usable_true_for_real_self_pid():
    assert _mod._ps_usable() is True


def test_ps_usable_false_when_ps_lstart_returns_none():
    assert _mod._ps_usable(ps_lstart=lambda pid: None) is False


def test_same_process_exact_match_is_same():
    assert _mod._same_process("Mon Jan  1 00:00:00 2024", "Mon Jan  1 00:00:00 2024") is True


def test_same_process_one_second_skew_is_same():
    assert _mod._same_process("Mon Jan  1 00:00:00 2024", "Mon Jan  1 00:00:01 2024") is True


def test_same_process_at_tolerance_boundary_is_same():
    assert _mod._same_process("Mon Jan  1 00:00:00 2024", "Mon Jan  1 00:00:02 2024") is True


def test_same_process_beyond_tolerance_boundary_is_different():
    assert _mod._same_process("Mon Jan  1 00:00:00 2024", "Mon Jan  1 00:00:03 2024") is False


def test_same_process_unparseable_stored_returns_none():
    assert _mod._same_process(None, "Mon Jan  1 00:00:00 2024") is None


def test_same_process_unparseable_live_returns_none():
    assert _mod._same_process("Mon Jan  1 00:00:00 2024", "garbage") is None


def test_entry_liveness_ps_unusable_returns_indeterminate():
    fake = _fake_ps_lstart({100: "Mon Jan  1 00:00:00 2024"})
    assert _mod._entry_liveness(100, "Mon Jan  1 00:00:00 2024", ps_lstart=fake, ps_usable=False) == "indeterminate"


def test_entry_liveness_dead_pid_returns_dead():
    fake = _fake_ps_lstart({})
    assert _mod._entry_liveness(100, "Mon Jan  1 00:00:00 2024", ps_lstart=fake, ps_usable=True) == "dead"


def test_entry_liveness_live_matching_procstart_returns_live():
    fake = _fake_ps_lstart({100: "Mon Jan  1 00:00:00 2024"})
    assert _mod._entry_liveness(100, "Mon Jan  1 00:00:00 2024", ps_lstart=fake, ps_usable=True) == "live"


def test_entry_liveness_live_mismatched_procstart_returns_dead():
    """PID-reuse guard: a live pid whose procStart doesn't match ours means a
    different process now holds it — our tracked process is gone."""
    fake = _fake_ps_lstart({100: "Mon Jan  1 00:10:00 2024"})
    assert _mod._entry_liveness(100, "Mon Jan  1 00:00:00 2024", ps_lstart=fake, ps_usable=True) == "dead"


def test_entry_liveness_tolerance_boundary_through_stubbed_ps_lstart():
    """The ±2s tolerance boundary already covered directly against
    _same_process (test_same_process_at_tolerance_boundary_is_same /
    test_same_process_beyond_tolerance_boundary_is_different) also holds
    through the stubbed _ps_lstart/_entry_liveness layer PID-reuse detection
    actually runs through, not just the raw pure function."""
    at_boundary = _fake_ps_lstart({100: "Mon Jan  1 00:00:02 2024"})
    assert _mod._entry_liveness(100, "Mon Jan  1 00:00:00 2024", ps_lstart=at_boundary, ps_usable=True) == "live"
    past_boundary = _fake_ps_lstart({100: "Mon Jan  1 00:00:03 2024"})
    assert _mod._entry_liveness(100, "Mon Jan  1 00:00:00 2024", ps_lstart=past_boundary, ps_usable=True) == "dead"


def test_entry_liveness_live_pid_missing_procstart_returns_indeterminate():
    fake = _fake_ps_lstart({100: "Mon Jan  1 00:00:00 2024"})
    assert _mod._entry_liveness(100, None, ps_lstart=fake, ps_usable=True) == "indeterminate"


# ---------------------------------------------------------------------------
# Source A — registry reading, including schema drift
# ---------------------------------------------------------------------------

def test_read_registry_happy_path(tmp_path):
    sessions_dir = tmp_path / "sessions"
    _write_registry_entry(sessions_dir, 100, sessionId="s1")
    entries, legacy, unparsed, found = _mod._read_registry([tmp_path])
    assert found is True
    assert unparsed == 0
    assert legacy == []
    assert [e.session_id for e in entries] == ["s1"]
    assert entries[0].pid == 100


def test_read_registry_missing_sessions_dir_reports_not_found(tmp_path):
    entries, legacy, unparsed, found = _mod._read_registry([tmp_path])
    assert found is False
    assert entries == []


def test_read_registry_non_json_registry_file_counts_unparsed(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    (sessions_dir / "100.json").write_text("not json{{{")
    entries, legacy, unparsed, found = _mod._read_registry([tmp_path])
    assert unparsed == 1
    assert entries == []


def test_read_registry_foreign_json_missing_core_fields_counts_unparsed(tmp_path):
    """A same-named-but-foreign JSON file (valid JSON, wrong shape) degrades
    to the unparsed bucket rather than crashing or being silently dropped."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    (sessions_dir / "100.json").write_text(json.dumps({"unrelated": "shape"}))
    entries, legacy, unparsed, found = _mod._read_registry([tmp_path])
    assert unparsed == 1
    assert entries == []


def test_read_registry_non_integer_pid_counts_unparsed(tmp_path):
    sessions_dir = tmp_path / "sessions"
    _write_json(sessions_dir / "100.json", {"sessionId": "s1", "pid": "not-a-pid"})
    entries, legacy, unparsed, found = _mod._read_registry([tmp_path])
    assert unparsed == 1
    assert entries == []


def test_read_registry_non_string_session_id_counts_unparsed_not_crash(tmp_path):
    """A sessionId of the wrong JSON type (a stray number, not a string)
    degrades to unparsed rather than crashing _sanitize_for_terminal, which
    iterates its input expecting a string."""
    sessions_dir = tmp_path / "sessions"
    _write_json(sessions_dir / "100.json", {"sessionId": 12345, "pid": 100})
    entries, legacy, unparsed, found = _mod._read_registry([tmp_path])
    assert unparsed == 1
    assert entries == []


def test_read_registry_top_level_json_array_counts_unparsed_not_crash(tmp_path):
    """A same-named-but-foreign JSON file whose top level is an array (not
    an object) must not crash on data.get(...)."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    (sessions_dir / "100.json").write_text(json.dumps([1, 2, 3]))
    entries, legacy, unparsed, found = _mod._read_registry([tmp_path])
    assert unparsed == 1
    assert entries == []


def test_read_registry_filename_pid_mismatch_prefers_field_and_flags_it(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    _write_json(sessions_dir / "999.json", _registry_entry_json(pid=100, sessionId="s1"))
    entries, *_ = _mod._read_registry([tmp_path])
    assert len(entries) == 1
    assert entries[0].pid == 100
    assert entries[0].pid_mismatch is True


def test_read_registry_legacy_bare_pid_files_collected_separately(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    (sessions_dir / "4242").write_text("some-session-id\n")
    entries, legacy, unparsed, found = _mod._read_registry([tmp_path])
    assert entries == []
    assert legacy == [sessions_dir / "4242"]


def test_read_registry_extra_field_ignored_by_construction(tmp_path):
    """Schema drift: an unexpected key (e.g. a future 'waitingFor' field) never reaches any output."""
    sessions_dir = tmp_path / "sessions"
    _write_registry_entry(sessions_dir, 100, sessionId="s1", waitingFor="something-unmodeled")
    entries, *_ = _mod._read_registry([tmp_path])
    assert len(entries) == 1
    assert not hasattr(entries[0], "waitingFor")


def test_read_registry_missing_optional_field_defaults_to_none(tmp_path):
    """Schema drift: a missing optional field degrades to None rather than crashing."""
    sessions_dir = tmp_path / "sessions"
    _write_json(sessions_dir / "100.json", {"sessionId": "s1", "pid": 100})
    entries, *_ = _mod._read_registry([tmp_path])
    assert entries[0].proc_start is None
    assert entries[0].cwd is None
    assert entries[0].version is None


def test_read_registry_renamed_field_degrades_to_default(tmp_path):
    """Schema drift: if a future CLI renames procStart, the entry still parses, just without it."""
    sessions_dir = tmp_path / "sessions"
    _write_json(sessions_dir / "100.json", {
        "sessionId": "s1", "pid": 100, "processStartedAt": "Mon Jan  1 00:00:00 2024",
    })
    entries, *_ = _mod._read_registry([tmp_path])
    assert len(entries) == 1
    assert entries[0].proc_start is None


# ---------------------------------------------------------------------------
# Source B — scheduled-task locks, union discovery
# ---------------------------------------------------------------------------

def test_read_lock_happy_path(tmp_path):
    lock_path = tmp_path / "proj" / ".claude" / "scheduled_tasks.lock"
    _write_lock(lock_path, sessionId="s2", pid=200)
    entry = _mod._read_lock(lock_path)
    assert entry is not None
    assert entry.session_id == "s2"
    assert entry.pid == 200


def test_read_lock_malformed_json_returns_none(tmp_path):
    lock_path = tmp_path / "scheduled_tasks.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("not json")
    assert _mod._read_lock(lock_path) is None


def test_read_lock_missing_pid_returns_none(tmp_path):
    lock_path = tmp_path / "scheduled_tasks.lock"
    _write_json(lock_path, {"sessionId": "s2"})
    assert _mod._read_lock(lock_path) is None


def test_read_lock_non_string_session_id_returns_none_not_crash(tmp_path):
    lock_path = tmp_path / "scheduled_tasks.lock"
    _write_json(lock_path, {"sessionId": 12345, "pid": 200})
    assert _mod._read_lock(lock_path) is None


def test_read_lock_top_level_json_array_returns_none_not_crash(tmp_path):
    lock_path = tmp_path / "scheduled_tasks.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps([1, 2, 3]))
    assert _mod._read_lock(lock_path) is None


def test_cwd_harvest_finds_lock_at_a_harvested_cwd(tmp_path):
    proj = tmp_path / "proj"
    lock_path = proj / ".claude" / "scheduled_tasks.lock"
    _write_lock(lock_path)
    assert _mod._cwd_harvest_lock_paths({str(proj)}) == [lock_path]


def test_cwd_harvest_ignores_cwd_with_no_lock(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    assert _mod._cwd_harvest_lock_paths({str(proj)}) == []


def test_find_scheduled_task_locks_discovers_lock_under_root(tmp_path):
    lock_path = tmp_path / "proj" / ".claude" / "scheduled_tasks.lock"
    _write_lock(lock_path)
    found, timed_out, elapsed = _mod._find_scheduled_task_locks(tmp_path)
    assert lock_path.resolve() in [p.resolve() for p in found]
    assert timed_out is False


def test_find_scheduled_task_locks_reports_timeout_without_raising(tmp_path):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 1))
    found, timed_out, elapsed = _mod._find_scheduled_task_locks(tmp_path, run=fake_run)
    assert found == []
    assert timed_out is True


def test_build_report_union_discovers_lock_missed_by_either_method_alone(tmp_path):
    """A harvest-only lock (reachable only because its cwd was seen in a
    transcript) and a find-only lock (never mentioned in any transcript)
    both surface from one run — proving the union, not either method alone."""
    config_dir_path = tmp_path / "config"
    (config_dir_path / "sessions").mkdir(parents=True)
    home_root = tmp_path / "home"
    home_root.mkdir()

    harvest_only_proj = tmp_path / "elsewhere" / "harvest-only-proj"
    harvest_lock = harvest_only_proj / ".claude" / "scheduled_tasks.lock"
    _write_lock(harvest_lock, sessionId="harvest-only", pid=_dead_pid())
    _write_transcript(
        config_dir_path / "projects" / "any-project-dir-name" / "harvest-only.jsonl",
        [_meta_record("harvest-only"), _cwd_record(str(harvest_only_proj), session_id="harvest-only")],
    )

    find_only_proj = home_root / "find-only-proj"
    find_only_lock = find_only_proj / ".claude" / "scheduled_tasks.lock"
    _write_lock(find_only_lock, sessionId="find-only", pid=_dead_pid())

    report = _mod.build_report(config_dirs=[config_dir_path], find_root=home_root)
    session_ids = {row.session_id for row in report.rows}
    assert "harvest-only" in session_ids
    assert "find-only" in session_ids


def test_build_report_never_calls_path_home_when_find_root_is_injected(tmp_path, monkeypatch):
    """Regression guard: build_report must thread find_root through
    explicitly rather than reading Path.home() internally."""
    def _forbidden_home():
        raise AssertionError("build_report must not call Path.home(); find_root must be threaded through explicitly")
    monkeypatch.setattr(Path, "home", staticmethod(_forbidden_home))
    config_dir_path = tmp_path / "config"
    (config_dir_path / "sessions").mkdir(parents=True)
    home_root = tmp_path / "home"
    home_root.mkdir()
    report = _mod.build_report(config_dirs=[config_dir_path], find_root=home_root)
    assert report is not None


def test_build_report_issues_one_batched_ps_call_not_one_per_entry(tmp_path, monkeypatch):
    """Regression guard: N registry entries needing a liveness check must
    cost one batched `ps` spawn (via _ps_lstart_batch), not N per-entry
    spawns. Wraps _ps_lstart_batch itself rather than subprocess.run, since
    _ps_lstart_batch's own `run` parameter is bound to subprocess.run at
    def time and would not observe a later monkeypatch of the module
    attribute."""
    sessions_dir = tmp_path / "config" / "sessions"
    for i in range(5):
        _write_registry_entry(sessions_dir, _dead_pid(), sessionId=f"s{i}")
    call_count = {"n": 0}
    real_batch = _mod._ps_lstart_batch

    def counting_batch(pids, **kwargs):
        call_count["n"] += 1
        return real_batch(pids, **kwargs)

    monkeypatch.setattr(_mod, "_ps_lstart_batch", counting_batch)
    _mod.build_report(config_dirs=[tmp_path / "config"], find_root=tmp_path / "home")
    assert call_count["n"] == 1


# ---------------------------------------------------------------------------
# Source C — transcript corpus, field allowlist, subagent surfacing
# ---------------------------------------------------------------------------

def test_read_transcript_head_finds_cwd_within_bound(tmp_path):
    path = tmp_path / "s1.jsonl"
    _write_transcript(path, [
        _meta_record("s1"),
        {"type": "user", "isMeta": True},
        _cwd_record("/tmp/proj", branch="feature-x", session_id="s1"),
    ])
    has_record, cwd, branch, ts = _mod._read_transcript_head(path, _mod._MAX_TRANSCRIPT_RECORDS)
    assert has_record is True
    assert cwd == "/tmp/proj"
    assert branch == "feature-x"


def test_read_transcript_head_exhausts_bound_without_finding_cwd(tmp_path):
    """A cwd sitting past the 12-record bound is never read — exhaustion
    yields an unknown cwd, not license to keep scanning."""
    path = tmp_path / "s1.jsonl"
    records = [_meta_record("s1")] + [{"type": "assistant", "n": i} for i in range(20)]
    records.append(_cwd_record("/tmp/proj", session_id="s1"))
    _write_transcript(path, records)
    has_record, cwd, branch, ts = _mod._read_transcript_head(path, _mod._MAX_TRANSCRIPT_RECORDS)
    assert has_record is True
    assert cwd is None


def test_read_transcript_head_zero_byte_file_has_no_record(tmp_path):
    path = tmp_path / "s1.jsonl"
    path.write_text("")
    has_record, cwd, branch, ts = _mod._read_transcript_head(path, _mod._MAX_TRANSCRIPT_RECORDS)
    assert has_record is False
    assert cwd is None


def test_read_transcript_head_all_malformed_lines_has_no_record(tmp_path):
    """A truncated/garbage transcript with no parseable record in the bound
    is treated the same as no transcript at all."""
    path = tmp_path / "s1.jsonl"
    path.write_text("{not json\n" * 5)
    has_record, cwd, branch, ts = _mod._read_transcript_head(path, _mod._MAX_TRANSCRIPT_RECORDS)
    assert has_record is False


def test_read_transcript_head_non_dict_json_line_does_not_crash(tmp_path):
    """A line that's valid JSON but not an object (e.g. a bare number) must
    not crash on the `"cwd" in rec` membership check."""
    path = tmp_path / "s1.jsonl"
    path.write_text("42\n" + json.dumps(_cwd_record("/tmp/proj", session_id="s1")) + "\n")
    has_record, cwd, branch, ts = _mod._read_transcript_head(path, _mod._MAX_TRANSCRIPT_RECORDS)
    assert has_record is True
    assert cwd == "/tmp/proj"


def test_scan_transcripts_main_file_present_sets_has_main_true(tmp_path):
    config_dir_path = tmp_path / "config"
    proj = config_dir_path / "projects" / "any-project-dir-name"
    _write_transcript(proj / "s1.jsonl", [_meta_record("s1"), _cwd_record("/tmp/proj", session_id="s1")])
    transcripts, cwds = _mod._scan_transcripts([config_dir_path])
    assert transcripts["s1"].has_main is True
    assert transcripts["s1"].cwd == "/tmp/proj"


def test_scan_transcripts_subagent_only_session_has_main_false(tmp_path):
    config_dir_path = tmp_path / "config"
    proj = config_dir_path / "projects" / "any-project-dir-name"
    _write_transcript(proj / "parent-id" / "subagents" / "agent-1.jsonl", [
        _meta_record("agent-1"), _cwd_record("/tmp/proj/nested", session_id="agent-1"),
    ])
    transcripts, cwds = _mod._scan_transcripts([config_dir_path])
    assert transcripts["parent-id"].has_main is False
    assert transcripts["parent-id"].subagent_count == 1
    assert "/tmp/proj/nested" in cwds


def test_scan_transcripts_harvests_cwd_from_both_main_and_subagent_files(tmp_path):
    config_dir_path = tmp_path / "config"
    proj = config_dir_path / "projects" / "any-project-dir-name"
    _write_transcript(proj / "s1.jsonl", [_meta_record("s1"), _cwd_record("/tmp/parent-proj", session_id="s1")])
    _write_transcript(proj / "s1" / "subagents" / "agent-1.jsonl", [
        _meta_record("agent-1"), _cwd_record("/tmp/nested-worktree", session_id="agent-1"),
    ])
    transcripts, cwds = _mod._scan_transcripts([config_dir_path])
    assert {"/tmp/parent-proj", "/tmp/nested-worktree"} <= cwds


def test_scan_transcripts_hostile_bytes_in_session_id_still_merges_main_and_subagent(tmp_path):
    """The main-loop jsonl.stem and the subagent-loop parent directory name
    are sanitized identically — a session id with control/escape bytes must
    still key to one merged TranscriptInfo, not split into two because one
    site sanitized and the other didn't."""
    hostile_id = "s1\x1b]0;pwned\x07"
    config_dir_path = tmp_path / "config"
    proj = config_dir_path / "projects" / "any-project-dir-name"
    _write_transcript(proj / f"{hostile_id}.jsonl", [
        _meta_record(hostile_id), _cwd_record("/tmp/parent-proj", session_id=hostile_id),
    ])
    _write_transcript(proj / hostile_id / "subagents" / "agent-1.jsonl", [
        _meta_record("agent-1"), _cwd_record("/tmp/nested-worktree", session_id="agent-1"),
    ])
    transcripts, _cwds = _mod._scan_transcripts([config_dir_path])
    sanitized_id = "s1]0;pwned"
    assert list(transcripts.keys()) == [sanitized_id]
    assert transcripts[sanitized_id].has_main is True
    assert transcripts[sanitized_id].subagent_count == 1
    transcripts = {"orphan": _transcript_info(session_id="orphan", last_activity=950.0, has_main=True)}
    assert _mod._near_boot_transcript_only_ids(transcripts, set(), boot_time=1000.0) == ["orphan"]


def test_near_boot_transcript_only_ids_excludes_known_session():
    transcripts = {"s1": _transcript_info(session_id="s1", last_activity=950.0, has_main=True)}
    assert _mod._near_boot_transcript_only_ids(transcripts, {"s1"}, boot_time=1000.0) == []


def test_near_boot_transcript_only_ids_includes_activity_within_widened_window():
    """A gap of 14000s before boot would have missed the old 600s window
    entirely — this is the empirical failure mode the widened window fixes."""
    transcripts = {"s1": _transcript_info(session_id="s1", last_activity=1000.0 - 14000.0, has_main=True)}
    assert _mod._near_boot_transcript_only_ids(transcripts, set(), boot_time=1000.0) == ["s1"]


def test_near_boot_transcript_only_ids_excludes_activity_outside_window():
    transcripts = {"s1": _transcript_info(session_id="s1", last_activity=1000.0 - 20000.0, has_main=True)}
    assert _mod._near_boot_transcript_only_ids(transcripts, set(), boot_time=1000.0) == []


def test_near_boot_transcript_only_ids_boundary_is_inclusive_one_instant_past_is_excluded():
    boot_time = 100000.0
    window = _mod._NEAR_BOOT_TRANSCRIPT_WINDOW_SECONDS
    at_boundary = {"s1": _transcript_info(session_id="s1", last_activity=boot_time - window, has_main=True)}
    just_past = {"s1": _transcript_info(session_id="s1", last_activity=boot_time - window - 0.001, has_main=True)}
    assert _mod._near_boot_transcript_only_ids(at_boundary, set(), boot_time=boot_time) == ["s1"]
    assert _mod._near_boot_transcript_only_ids(just_past, set(), boot_time=boot_time) == []


def test_near_boot_transcript_only_ids_none_when_boot_time_unknown():
    transcripts = {"s1": _transcript_info(session_id="s1", last_activity=950.0, has_main=True)}
    assert _mod._near_boot_transcript_only_ids(transcripts, set(), boot_time=None) == []


# ---------------------------------------------------------------------------
# Classification precedence
# ---------------------------------------------------------------------------

def test_classify_live_pid_yields_clean_exit_not_crash_evidence():
    entry = _registry_entry(pid=100, proc_start="Mon Jan  1 00:00:00 2024", mtime=500.0)
    fake = _fake_ps_lstart({100: "Mon Jan  1 00:00:00 2024"})
    row = _mod._classify_session("s1", [entry], [], None, boot_time=1000.0, ps_lstart=fake, ps_usable=True)
    assert row.classification == _mod.CLASS_CLEAN_EXIT


def test_classify_registry_dead_before_boot_with_transcript_is_resumable():
    entry = _registry_entry(mtime=500.0)
    transcript = _transcript_info(last_activity=500.0, has_main=True)
    row = _mod._classify_session(
        "s1", [entry], [], transcript, boot_time=1000.0, ps_lstart=_fake_ps_lstart({}), ps_usable=True,
    )
    assert row.classification == _mod.CLASS_RESUMABLE
    assert row.cwd == "/tmp/proj"


def test_classify_registry_dead_before_boot_no_transcript_is_crashed_no_transcript():
    entry = _registry_entry(mtime=500.0)
    row = _mod._classify_session(
        "s1", [entry], [], None, boot_time=1000.0, ps_lstart=_fake_ps_lstart({}), ps_usable=True,
    )
    assert row.classification == _mod.CLASS_CRASHED_NO_TRANSCRIPT


def test_classify_registry_dead_after_boot_is_unknown_not_crash_evidence():
    entry = _registry_entry(mtime=1500.0)
    row = _mod._classify_session(
        "s1", [entry], [], None, boot_time=1000.0, ps_lstart=_fake_ps_lstart({}), ps_usable=True,
    )
    assert row.classification == _mod.CLASS_UNKNOWN
    assert "after boot" in row.detail


def test_classify_registry_dead_unknown_mtime_is_unknown_not_crash_evidence():
    """A stat() failure on the registry file degrades to mtime=None — an
    unknown-age dead entry must never be silently treated as 'before boot'
    and reported as crash evidence just because None used to coerce to 0.0."""
    entry = _registry_entry(mtime=None)
    row = _mod._classify_session(
        "s1", [entry], [], None, boot_time=1000.0, ps_lstart=_fake_ps_lstart({}), ps_usable=True,
    )
    assert row.classification == _mod.CLASS_UNKNOWN
    assert "modification time" in row.detail


def test_classify_lock_dead_no_registry_with_transcript_is_resumable():
    lock = _lock_entry(mtime=500.0)
    transcript = _transcript_info(session_id="s2", last_activity=500.0, has_main=True)
    row = _mod._classify_session(
        "s2", [], [lock], transcript, boot_time=1000.0, ps_lstart=_fake_ps_lstart({}), ps_usable=True,
    )
    assert row.classification == _mod.CLASS_RESUMABLE


def test_classify_lock_dead_no_registry_no_transcript_is_crashed_no_transcript():
    lock = _lock_entry(mtime=500.0)
    row = _mod._classify_session(
        "s2", [], [lock], None, boot_time=1000.0, ps_lstart=_fake_ps_lstart({}), ps_usable=True,
    )
    assert row.classification == _mod.CLASS_CRASHED_NO_TRANSCRIPT


def test_classify_lock_sessionid_with_neither_registry_nor_transcript(tmp_path):
    """A lock whose sessionId has no registry entry and no transcript at
    all — this was the only surviving proof of a crash in the incident
    this tool exists to cover."""
    lock = _lock_entry(session_id="orphan-lock", mtime=500.0)
    row = _mod._classify_session(
        "orphan-lock", [], [lock], None, boot_time=1000.0, ps_lstart=_fake_ps_lstart({}), ps_usable=True,
    )
    assert row.classification == _mod.CLASS_CRASHED_NO_TRANSCRIPT


def test_classify_registry_procstart_unparseable_is_unknown():
    entry = _registry_entry(pid=100, proc_start=None, mtime=1500.0)
    fake = _fake_ps_lstart({100: "Mon Jan  1 00:00:05 2024"})
    row = _mod._classify_session("s1", [entry], [], None, boot_time=1000.0, ps_lstart=fake, ps_usable=True)
    assert row.classification == _mod.CLASS_UNKNOWN
    assert "procStart" in row.detail


def test_classify_lock_procstart_unparseable_is_unknown():
    lock = _lock_entry(pid=200, proc_start=None, mtime=1500.0)
    fake = _fake_ps_lstart({200: "Mon Jan  1 00:00:05 2024"})
    row = _mod._classify_session("s2", [], [lock], None, boot_time=1000.0, ps_lstart=fake, ps_usable=True)
    assert row.classification == _mod.CLASS_UNKNOWN


def test_classify_ps_unusable_is_unknown_never_crashed():
    """ps being unusable must never produce a false crash report."""
    entry = _registry_entry(mtime=500.0)
    transcript = _transcript_info(last_activity=500.0, has_main=True)
    row = _mod._classify_session(
        "s1", [entry], [], transcript, boot_time=1000.0, ps_lstart=_fake_ps_lstart({}), ps_usable=False,
    )
    assert row.classification == _mod.CLASS_UNKNOWN


def test_classify_boot_time_unknown_is_unknown():
    entry = _registry_entry(mtime=500.0)
    row = _mod._classify_session(
        "s1", [entry], [], None, boot_time=None, ps_lstart=_fake_ps_lstart({}), ps_usable=True,
    )
    assert row.classification == _mod.CLASS_UNKNOWN


def test_classify_near_boot_transcript_only_session_is_possible_crash():
    transcript = _transcript_info(session_id="s1", last_activity=950.0, has_main=True)
    row = _mod._classify_session(
        "s1", [], [], transcript, boot_time=1000.0, ps_lstart=_fake_ps_lstart({}), ps_usable=True,
    )
    assert row.classification == _mod.CLASS_POSSIBLE_CRASH
    assert "within 4h before the last boot" in row.detail
    assert "no registry or lock corroboration" in row.detail


def test_classify_near_boot_transcript_only_session_detail_reflects_custom_window():
    transcript = _transcript_info(session_id="s1", last_activity=950.0, has_main=True)
    row = _mod._classify_session(
        "s1", [], [], transcript, boot_time=1000.0, ps_lstart=_fake_ps_lstart({}), ps_usable=True,
        near_boot_window_seconds=72 * 3600,
    )
    assert row.classification == _mod.CLASS_POSSIBLE_CRASH
    assert "within 72h before the last boot" in row.detail


def test_classify_near_boot_transcript_only_session_detail_formats_fractional_window():
    transcript = _transcript_info(session_id="s1", last_activity=950.0, has_main=True)
    row = _mod._classify_session(
        "s1", [], [], transcript, boot_time=1000.0, ps_lstart=_fake_ps_lstart({}), ps_usable=True,
        near_boot_window_seconds=1.5 * 3600,
    )
    assert "within 1.5h before the last boot" in row.detail


def test_classify_subagent_only_transcript_does_not_count_as_resumable():
    """A subagent transcript with no main-thread transcript cannot be
    --resume'd; classification stays crashed-no-transcript, with a note."""
    entry = _registry_entry(mtime=500.0)
    transcript = _transcript_info(has_main=False, subagent_count=2, cwd=None, git_branch=None)
    row = _mod._classify_session(
        "s1", [entry], [], transcript, boot_time=1000.0, ps_lstart=_fake_ps_lstart({}), ps_usable=True,
    )
    assert row.classification == _mod.CLASS_CRASHED_NO_TRANSCRIPT
    assert "subagent" in row.detail


def test_classify_collapses_multiple_registry_entries_alive_wins():
    dead_entry = _registry_entry(pid=100, mtime=500.0, proc_start="Mon Jan  1 00:00:00 2024")
    alive_entry = _registry_entry(pid=200, mtime=1500.0, proc_start="Mon Jan  1 00:20:00 2024")
    fake = _fake_ps_lstart({200: "Mon Jan  1 00:20:00 2024"})
    row = _mod._classify_session(
        "s1", [dead_entry, alive_entry], [], None, boot_time=1000.0, ps_lstart=fake, ps_usable=True,
    )
    assert row.classification == _mod.CLASS_CLEAN_EXIT
    assert row.entry_count == 2


def test_classify_registry_entries_take_precedence_over_lock_entries():
    registry_dead_before = _registry_entry(pid=100, mtime=500.0)
    lock_dead = _lock_entry(pid=200, mtime=1500.0)
    row = _mod._classify_session(
        "s1", [registry_dead_before], [lock_dead], None,
        boot_time=1000.0, ps_lstart=_fake_ps_lstart({}), ps_usable=True,
    )
    assert row.classification == _mod.CLASS_CRASHED_NO_TRANSCRIPT
    assert row.entry_count == 2


def test_classify_unknown_row_names_the_uncertainty():
    """Every unknown row must explain what made it uncertain, not just say 'unknown'."""
    entry = _registry_entry(mtime=1500.0)
    row = _mod._classify_session(
        "s1", [entry], [], None, boot_time=1000.0, ps_lstart=_fake_ps_lstart({}), ps_usable=True,
    )
    assert row.classification == _mod.CLASS_UNKNOWN
    assert row.detail and row.detail != "unknown"


def test_classify_cwd_missing_on_disk_is_flagged():
    entry = _registry_entry(mtime=500.0, cwd="/this/path/does/not/exist/on/this/machine")
    transcript = _transcript_info(cwd=None, git_branch=None, last_activity=500.0, has_main=True)
    row = _mod._classify_session(
        "s1", [entry], [], transcript, boot_time=1000.0, ps_lstart=_fake_ps_lstart({}), ps_usable=True,
    )
    assert row.cwd_missing is True


def test_classify_unaffected_by_hostile_timezone(monkeypatch):
    """A registry entry for the current (alive) process, read under a
    non-UTC TZ, still classifies as clean-exit — proving the TZ override
    inside _ps_lstart is what makes this correct, not the test runner's own
    UTC default. See test_ps_lstart_ignores_hostile_ambient_timezone for why
    the locale half of a hostile-environment injection is omitted here."""
    pid = os.getpid()
    proc_start = _mod._ps_lstart(pid)
    assert proc_start is not None
    entry = _registry_entry(pid=pid, proc_start=proc_start, mtime=time.time())
    monkeypatch.setenv("TZ", "<+05>-5")
    row = _mod._classify_session(
        "s1", [entry], [], None, boot_time=0.0, ps_lstart=_mod._ps_lstart, ps_usable=True,
    )
    assert row.classification == _mod.CLASS_CLEAN_EXIT


# ---------------------------------------------------------------------------
# build_report — schema drift, version drift, mismatches, legacy bare-pid
# ---------------------------------------------------------------------------

def test_build_report_flags_version_drift(tmp_path):
    sessions_dir = tmp_path / "config" / "sessions"
    _write_registry_entry(sessions_dir, _dead_pid(), sessionId="s1", version="9.9.9")
    report = _mod.build_report(config_dirs=[tmp_path / "config"], find_root=tmp_path / "home")
    assert "9.9.9" in report.version_drift


def test_build_report_flags_pid_filename_mismatch(tmp_path):
    sessions_dir = tmp_path / "config" / "sessions"
    sessions_dir.mkdir(parents=True)
    dead = _dead_pid()
    _write_json(sessions_dir / "999999.json", _registry_entry_json(pid=dead, sessionId="s1"))
    report = _mod.build_report(config_dirs=[tmp_path / "config"], find_root=tmp_path / "home")
    assert len(report.pid_mismatches) == 1


def test_build_report_legacy_bare_pid_dead_reported(tmp_path):
    sessions_dir = tmp_path / "config" / "sessions"
    sessions_dir.mkdir(parents=True)
    dead = _dead_pid()
    (sessions_dir / str(dead)).write_text("some-session-id\n")
    report = _mod.build_report(config_dirs=[tmp_path / "config"], find_root=tmp_path / "home")
    assert sessions_dir / str(dead) in report.legacy_bare_pid_dead


def test_build_report_legacy_bare_pid_live_not_reported(tmp_path):
    """A live legacy lookup file must never be listed for deletion — it's
    active infrastructure require-*.sh gates depend on."""
    sessions_dir = tmp_path / "config" / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / str(os.getpid())).write_text("some-session-id\n")
    report = _mod.build_report(config_dirs=[tmp_path / "config"], find_root=tmp_path / "home")
    assert report.legacy_bare_pid_dead == []


def test_build_report_no_sessions_dir_at_all_produces_clean_report(tmp_path):
    config_dir_path = tmp_path / "config"
    config_dir_path.mkdir()
    report = _mod.build_report(config_dirs=[config_dir_path], find_root=tmp_path / "home")
    assert report.any_sessions_dir_found is False
    output = _mod.render_report(report, redact=False)
    assert "Resumable (0)" in output


def test_build_report_stores_config_dirs_on_returned_report(tmp_path):
    """The config_dirs list passed in is main()'s fully-resolved list -- the
    tests that spy on build_report's config_dirs kwarg only prove what
    main() sends in, not that build_report stores it unmodified on the
    returned Report. Closes that passthrough gap directly."""
    first = tmp_path / "config-a"
    first.mkdir()
    second = tmp_path / "config-b"
    second.mkdir()
    report = _mod.build_report(config_dirs=[first, second], find_root=tmp_path / "home")
    assert report.config_dirs == [first, second]


def test_build_report_foreign_json_in_sessions_dir_produces_clean_report(tmp_path):
    sessions_dir = tmp_path / "config" / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "12345.json").write_text(json.dumps({"totally": "unrelated", "shape": True}))
    report = _mod.build_report(config_dirs=[tmp_path / "config"], find_root=tmp_path / "home")
    assert report.unparsed_registry == 1
    output = _mod.render_report(report, redact=False)
    assert "Resumable (0)" in output


def test_build_report_and_render_write_nothing_to_tmp_path(tmp_path):
    sessions_dir = tmp_path / "config" / "sessions"
    _write_registry_entry(sessions_dir, _dead_pid(), sessionId="s1")
    home_root = tmp_path / "home"
    home_root.mkdir()

    def _snapshot():
        return sorted(
            (str(p.relative_to(tmp_path)), p.stat().st_mtime) for p in tmp_path.rglob("*") if p.is_file()
        )

    before = _snapshot()
    report = _mod.build_report(config_dirs=[tmp_path / "config"], find_root=home_root)
    _mod.render_report(report, redact=False)
    _mod.render_report(report, redact=True)
    after = _snapshot()
    assert before == after


# ---------------------------------------------------------------------------
# Sanitization wired at real ingestion sites, not just correct in isolation
# ---------------------------------------------------------------------------

def test_build_report_sanitizes_hostile_control_bytes_in_version_field(tmp_path):
    """version used to bypass _sanitize_for_terminal and reach version_drift's
    rendered banner unredacted — including under --redact, since version_drift
    is never routed through the ordinal-mapping the rest of --redact relies on."""
    hostile_version = "9.9.9\x1b]0;pwned\x07"
    sessions_dir = tmp_path / "config" / "sessions"
    _write_registry_entry(sessions_dir, _dead_pid(), sessionId="s1", version=hostile_version)
    report = _mod.build_report(config_dirs=[tmp_path / "config"], find_root=tmp_path / "home")
    assert report.version_drift == ["9.9.9]0;pwned"]
    for redact in (False, True):
        output = _mod.render_report(report, redact=redact)
        assert "\x1b" not in output
        assert "\x07" not in output


def test_build_report_sanitizes_hostile_control_bytes_in_transcript_filename_session_id(tmp_path):
    """A near-boot-only orphan session (no registry or lock entry at all)
    takes its session id straight from the transcript filename stem — POSIX
    filenames permit raw ESC/BEL bytes that a JSON string field never would,
    and that id must not reach rendered output unsanitized."""
    hostile_stem = "orphan\x1b]0;pwned\x07"
    config_dir_path = tmp_path / "config"
    transcript_path = config_dir_path / "projects" / "any-project-dir-name" / f"{hostile_stem}.jsonl"
    _write_transcript(transcript_path, [
        _meta_record(hostile_stem), _cwd_record("/tmp/orphan-proj", session_id=hostile_stem),
    ])
    near_boot_mtime = 950.0
    os.utime(transcript_path, (near_boot_mtime, near_boot_mtime))
    report = _mod.build_report(
        config_dirs=[config_dir_path], find_root=tmp_path / "home", boot_time_fn=lambda: 1000.0,
    )
    output = _mod.render_report(report, redact=False)
    assert "\x1b" not in output
    assert "\x07" not in output
    assert "orphan]0;pwned" in output


def test_build_report_sanitizes_control_bytes_across_cwd_branch_and_session_id_ingestion(tmp_path):
    """Integration-level: a control/escape byte embedded in cwd, gitBranch,
    and sessionId across the registry and transcript sources must not survive
    the real _read_registry/_scan_transcripts ingestion path — this exercises
    the sanitizer at its actual call sites, not just as a pure function."""
    hostile_session_id = "s1\x1b]0;pwned\x07"
    hostile_cwd = "/tmp/evil\x1b]0;proj\x07"
    hostile_branch = "feature\x1b]0;branch\x07"
    config_dir_path = tmp_path / "config"
    sessions_dir = config_dir_path / "sessions"
    dead = _dead_pid()
    _write_registry_entry(sessions_dir, dead, sessionId=hostile_session_id, cwd=hostile_cwd)
    transcript_path = config_dir_path / "projects" / "any-project-dir-name" / f"{hostile_session_id}.jsonl"
    _write_transcript(transcript_path, [
        _meta_record(hostile_session_id),
        _cwd_record(hostile_cwd, branch=hostile_branch, session_id=hostile_session_id),
    ])
    far_past = 1000
    os.utime(sessions_dir / f"{dead}.json", (far_past, far_past))
    os.utime(transcript_path, (far_past, far_past))
    report = _mod.build_report(
        config_dirs=[config_dir_path], find_root=tmp_path / "home", boot_time_fn=lambda: 2000.0,
    )
    for redact in (False, True):
        output = _mod.render_report(report, redact=redact)
        assert "\x1b" not in output
        assert "\x07" not in output


# ---------------------------------------------------------------------------
# No prompt leakage
# ---------------------------------------------------------------------------

def test_report_never_leaks_transcript_message_content(tmp_path):
    secret_marker = "TOTALLY-SECRET-FIRST-PROMPT-MARKER-9f3a"
    sessions_dir = tmp_path / "config" / "sessions"
    proj = tmp_path / "proj"
    dead = _dead_pid()
    _write_registry_entry(sessions_dir, dead, sessionId="s1", cwd=str(proj))
    transcript_path = tmp_path / "config" / "projects" / "any-project-dir-name" / "s1.jsonl"
    _write_transcript(transcript_path, [
        _meta_record("s1"),
        {
            "type": "user", "cwd": str(proj), "gitBranch": "main",
            "timestamp": "2024-01-01T00:00:00Z", "sessionId": "s1",
            "message": {"role": "user", "content": secret_marker},
        },
    ])
    far_past = 1000
    os.utime(sessions_dir / f"{dead}.json", (far_past, far_past))
    os.utime(transcript_path, (far_past, far_past))
    report = _mod.build_report(
        config_dirs=[tmp_path / "config"], find_root=tmp_path / "home", boot_time_fn=lambda: 2000.0,
    )
    output_unredacted = _mod.render_report(report, redact=False)
    output_redacted = _mod.render_report(report, redact=True)
    assert secret_marker not in output_unredacted
    assert secret_marker not in output_redacted


def test_report_never_leaks_transcript_tooluseresult_field(tmp_path):
    """_read_transcript_head's own docstring names message, content, AND
    toolUseResult as excluded — the sibling test above only covers
    message.content, this covers toolUseResult."""
    secret_marker = "TOTALLY-SECRET-TOOL-RESULT-MARKER-7c2e"
    sessions_dir = tmp_path / "config" / "sessions"
    proj = tmp_path / "proj"
    dead = _dead_pid()
    _write_registry_entry(sessions_dir, dead, sessionId="s1", cwd=str(proj))
    transcript_path = tmp_path / "config" / "projects" / "any-project-dir-name" / "s1.jsonl"
    _write_transcript(transcript_path, [
        _meta_record("s1"),
        {
            "type": "user", "cwd": str(proj), "gitBranch": "main",
            "timestamp": "2024-01-01T00:00:00Z", "sessionId": "s1",
            "toolUseResult": secret_marker,
        },
    ])
    far_past = 1000
    os.utime(sessions_dir / f"{dead}.json", (far_past, far_past))
    os.utime(transcript_path, (far_past, far_past))
    report = _mod.build_report(
        config_dirs=[tmp_path / "config"], find_root=tmp_path / "home", boot_time_fn=lambda: 2000.0,
    )
    output_unredacted = _mod.render_report(report, redact=False)
    output_redacted = _mod.render_report(report, redact=True)
    assert secret_marker not in output_unredacted
    assert secret_marker not in output_redacted


# ---------------------------------------------------------------------------
# --redact
# ---------------------------------------------------------------------------

def test_render_report_redact_maps_cwd_and_session_to_ordinals_and_drops_branch():
    row = _mod.SessionRow(
        session_id="sess-one", classification=_mod.CLASS_RESUMABLE,
        cwd="/repo/example-project", git_branch="feature-x", last_activity=1000.0,
        detail="test detail", entry_count=1, cwd_missing=False,
    )
    report = _blank_report(rows=[row])
    output = _mod.render_report(report, redact=True)
    assert "sess-one" not in output
    assert "/repo/example-project" not in output
    assert "feature-x" not in output
    assert "session-1" in output
    assert "project-1" in output


def test_render_report_unredacted_preserves_real_values():
    row = _mod.SessionRow(
        session_id="sess-one", classification=_mod.CLASS_RESUMABLE,
        cwd="/repo/example-project", git_branch="feature-x", last_activity=1000.0,
        detail="test detail", entry_count=1, cwd_missing=False,
    )
    report = _blank_report(rows=[row])
    output = _mod.render_report(report, redact=False)
    assert "sess-one" in output
    assert "/repo/example-project" in output
    assert "feature-x" in output


def test_render_report_always_prints_not_publish_safe_footer_either_way():
    output_unredacted = _mod.render_report(_blank_report(), redact=False)
    output_redacted = _mod.render_report(_blank_report(), redact=True)
    assert "publish-safe" in output_unredacted.lower()
    assert "publish-safe" in output_redacted.lower()


def test_redact_output_matches_no_structural_detector_regex(tmp_path):
    row = _mod.SessionRow(
        session_id="sess-deadbeef", classification=_mod.CLASS_RESUMABLE,
        cwd="/repo/example/private-project", git_branch="feature/secret-work",
        last_activity=time.time(), detail="registry entry written before boot; transcript found.",
        entry_count=1, cwd_missing=False,
    )
    report = _blank_report(rows=[row], config_dirs=[Path("/repo/example/.claude")])
    output = _mod.render_report(report, redact=True)
    out_file = tmp_path / "redacted-output.txt"
    out_file.write_text(output)
    patterns = _structural_detector_patterns()
    for label, pattern in patterns.items():
        result = subprocess.run(["grep", "-Eq", "--", pattern, str(out_file)])
        assert result.returncode == 1, f"detector {label!r} matched (rc={result.returncode}) against pattern {pattern!r}"


# ---------------------------------------------------------------------------
# config_dirs_explicit -- gates the unredacted "Config directories scanned"
# header's raw-path form on whether the extras came from a typed --config-dir
# rather than the ~/.claude/transcript-config-dirs default
# ---------------------------------------------------------------------------

def test_render_report_declared_roots_default_shows_count_not_paths():
    """Beyond the default, config_dirs came from the declared-roots default
    (no --config-dir typed this run) -- the header must not print those
    paths even unredacted, since --redact is opt-in and this leak would
    happen before the operator ever makes that choice."""
    report = _blank_report(config_dirs=[Path("/fake/default-account"), Path("/fake/declared-account")])
    output = _mod.render_report(report, redact=False, config_dirs_explicit=False)
    assert "Config directories scanned: 2" in output
    assert "/fake/default-account" not in output
    assert "/fake/declared-account" not in output


def test_render_report_explicit_config_dir_still_shows_raw_paths_unredacted():
    """An explicit --config-dir is a path the operator already typed this
    run -- printing it back unredacted is the pre-declared-roots-default
    behavior, preserved when config_dirs_explicit is True."""
    report = _blank_report(config_dirs=[Path("/fake/default-account"), Path("/fake/explicit-account")])
    output = _mod.render_report(report, redact=False, config_dirs_explicit=True)
    assert "Config directories scanned: /fake/default-account, /fake/explicit-account" in output


def test_render_report_redact_shows_count_regardless_of_config_dirs_explicit():
    report = _blank_report(config_dirs=[Path("/fake/default-account"), Path("/fake/other-account")])
    for explicit in (False, True):
        output = _mod.render_report(report, redact=True, config_dirs_explicit=explicit)
        assert "Config directories scanned: 2" in output
        assert "/fake/default-account" not in output
        assert "/fake/other-account" not in output


def test_render_report_single_root_shows_raw_path_regardless_of_config_dirs_explicit():
    """A single config dir is always just the operator's own default,
    never a declared-roots entry -- config_dirs_explicit doesn't gate it."""
    report = _blank_report(config_dirs=[Path("/fake/only-account")])
    output = _mod.render_report(report, redact=False, config_dirs_explicit=False)
    assert "Config directories scanned: /fake/only-account" in output


# ---------------------------------------------------------------------------
# Roots-file-state note on the "Config directories scanned" line -- N alone
# can't distinguish absent / declared-nothing / --config-dir override, so the
# note is checked in both the raw-paths (--redact off) and count (--redact on)
# forms of show_raw_config_dirs. The populated-and-contributing case is
# already covered by test_render_report_declared_roots_default_shows_count_not_paths
# and test_render_report_redact_shows_count_regardless_of_config_dirs_explicit
# above: with config_dirs_explicit=False and more than one root, only the
# count branch is ever reachable (show_raw_config_dirs requires explicit or
# a single root), and root_count != 1 already suppresses the note.
# ---------------------------------------------------------------------------

def test_render_report_notes_absent_roots_file_unredacted_raw_path_branch(monkeypatch, tmp_path):
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(tmp_path / "does-not-exist"))
    report = _blank_report(config_dirs=[Path("/fake/only-account")])
    output = _mod.render_report(report, redact=False, config_dirs_explicit=False)
    assert "no ~/.claude/transcript-config-dirs declared" in output


def test_render_report_notes_absent_roots_file_redacted_count_branch(monkeypatch, tmp_path):
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(tmp_path / "does-not-exist"))
    report = _blank_report(config_dirs=[Path("/fake/only-account")])
    output = _mod.render_report(report, redact=True, config_dirs_explicit=False)
    assert "Config directories scanned: 1" in output
    assert "no ~/.claude/transcript-config-dirs declared" in output


def test_render_report_comments_only_roots_file_distinct_from_absent_unredacted(monkeypatch, tmp_path):
    """A file that exists but declares nothing usable must not read as
    identical to no file at all, in the raw-paths (--redact off) branch."""
    roots_file = tmp_path / "roots"
    roots_file.write_text("# nothing declared yet\n")
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
    report = _blank_report(config_dirs=[Path("/fake/only-account")])
    output = _mod.render_report(report, redact=False, config_dirs_explicit=False)
    assert "declared; contributed no additional directories" in output
    assert "no ~/.claude/transcript-config-dirs declared" not in output


def test_render_report_comments_only_roots_file_distinct_from_absent_redacted(monkeypatch, tmp_path):
    """Same distinction, in the count (--redact on) branch."""
    roots_file = tmp_path / "roots"
    roots_file.write_text("# nothing declared yet\n")
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
    report = _blank_report(config_dirs=[Path("/fake/only-account")])
    output = _mod.render_report(report, redact=True, config_dirs_explicit=False)
    assert "Config directories scanned: 1" in output
    assert "declared; contributed no additional directories" in output
    assert "no ~/.claude/transcript-config-dirs declared" not in output


def test_render_report_notes_unreadable_roots_file(monkeypatch, tmp_path):
    """A directory at the roots-file path raises IsADirectoryError (an
    OSError subclass) on read_text() -- simulates an unreadable file without
    chmod, which silently degrades under a root-executing test runner (see
    test__config_dir.py's test_file_state_is_unreadable_when_read_raises_oserror
    for the same rationale)."""
    roots_file_as_dir = tmp_path / "roots-is-a-directory"
    roots_file_as_dir.mkdir()
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file_as_dir))
    report = _blank_report(config_dirs=[Path("/fake/only-account")])
    output = _mod.render_report(report, redact=False, config_dirs_explicit=False)
    assert "present but unreadable" in output


def test_render_report_explicit_config_dir_note_overrides_populated_roots_file(monkeypatch, tmp_path):
    """config_dirs_explicit=True must select the override note before the
    root_count check is ever reached, even with more than one root and a
    populated, contributing roots file -- _config_dirs_scanned_note checks
    config_dirs_explicit first, unconditionally. A single config_dirs entry
    can't pin that ordering: at root_count=1 a reordered (buggy)
    implementation that checks root_count first produces the identical
    output, since root_count=1 also satisfies its own `!= 1` guard -- only
    root_count>1 makes the two orderings diverge."""
    declared_dir = tmp_path / "declared-config"
    (declared_dir / "projects").mkdir(parents=True)
    roots_file = tmp_path / "roots"
    roots_file.write_text(f"{declared_dir}\n")
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
    report = _blank_report(config_dirs=[Path("/fake/explicit-account-1"), Path("/fake/explicit-account-2")])
    output = _mod.render_report(report, redact=False, config_dirs_explicit=True)
    assert "--config-dir passed explicitly" in output
    assert "declared; contributed no additional directories" not in output
    assert "no ~/.claude/transcript-config-dirs declared" not in output


# ---------------------------------------------------------------------------
# Per-row account-N tagging -- only once multi-root, reusing _assign_ordinal
# ---------------------------------------------------------------------------

def test_render_report_tags_rows_with_account_ordinal_when_multi_root():
    account_a = Path("/fake/account-a")
    account_b = Path("/fake/account-b")
    row_a = _mod.SessionRow(
        session_id="sess-a", classification=_mod.CLASS_RESUMABLE,
        cwd="/tmp/proj-a", git_branch="main", last_activity=200.0,
        detail="test detail", entry_count=1, cwd_missing=False, config_dir=account_a,
    )
    row_b = _mod.SessionRow(
        session_id="sess-b", classification=_mod.CLASS_CRASHED_NO_TRANSCRIPT,
        cwd="/tmp/proj-b", git_branch="main", last_activity=100.0,
        detail="test detail", entry_count=1, cwd_missing=False, config_dir=account_b,
    )
    report = _blank_report(rows=[row_a, row_b], config_dirs=[account_a, account_b])
    output = _mod.render_report(report, redact=False, config_dirs_explicit=True)
    # account-1/account-2 assignment is sorted by resolved path, not scan
    # order -- account_a < account_b alphabetically.
    assert "account-1, last activity" in output
    assert "[account-2]" in output


def test_render_report_account_ordinal_present_under_redact_too():
    """Per-row account-N tagging is required in both redacted and
    unredacted mode -- unlike cwd/session-id ordinals, it must not be
    gated on --redact."""
    account_a = Path("/fake/account-a")
    account_b = Path("/fake/account-b")
    row = _mod.SessionRow(
        session_id="sess-a", classification=_mod.CLASS_RESUMABLE,
        cwd="/tmp/proj-a", git_branch="main", last_activity=200.0,
        detail="test detail", entry_count=1, cwd_missing=False, config_dir=account_a,
    )
    report = _blank_report(rows=[row], config_dirs=[account_a, account_b])
    output = _mod.render_report(report, redact=True, config_dirs_explicit=False)
    assert "account-1, last activity" in output


def test_render_report_omits_account_tag_at_single_root():
    """At a single config dir every row belongs to the only declared
    account -- tagging it would add noise, not signal."""
    row = _mod.SessionRow(
        session_id="sess-a", classification=_mod.CLASS_RESUMABLE,
        cwd="/tmp/proj-a", git_branch="main", last_activity=200.0,
        detail="test detail", entry_count=1, cwd_missing=False, config_dir=Path("/fake/config"),
    )
    report = _blank_report(rows=[row])
    output = _mod.render_report(report, redact=False, config_dirs_explicit=False)
    assert "account-" not in output


# ---------------------------------------------------------------------------
# render_report — "Possible crash" tier
# ---------------------------------------------------------------------------

def test_render_report_possible_crash_section_lists_rows_sorted_by_recency():
    older = _mod.SessionRow(
        session_id="s-old", classification=_mod.CLASS_POSSIBLE_CRASH,
        cwd="/tmp/old-proj", git_branch="main", last_activity=100.0,
        detail="only a transcript exists", entry_count=0, cwd_missing=False,
    )
    newer = _mod.SessionRow(
        session_id="s-new", classification=_mod.CLASS_POSSIBLE_CRASH,
        cwd="/tmp/new-proj", git_branch="main", last_activity=200.0,
        detail="only a transcript exists", entry_count=0, cwd_missing=False,
    )
    output = _mod.render_report(_blank_report(rows=[older, newer]), redact=False)
    assert "## Possible crash — transcript only (2)" in output
    assert output.index("s-new") < output.index("s-old")


def test_render_report_possible_crash_row_not_duplicated_into_unknown_section():
    """A missed exclude-from-other_groups filter would render this row into
    both its own section and ## Unknown; catch that instead of the row
    merely existing somewhere in the output."""
    row = _mod.SessionRow(
        session_id="poss-crash-sess", classification=_mod.CLASS_POSSIBLE_CRASH,
        cwd="/tmp/only-transcript-proj", git_branch="main", last_activity=100.0,
        detail="only a transcript exists", entry_count=0, cwd_missing=False,
    )
    output = _mod.render_report(_blank_report(rows=[row]), redact=False)
    assert output.count("poss-crash-sess") == 1
    unknown_section = output.split("## Unknown")[1].split("## ")[0]
    assert "poss-crash-sess" not in unknown_section


def test_render_report_possible_crash_redact_maps_cwd_and_session_to_ordinals_and_drops_branch():
    row = _mod.SessionRow(
        session_id="sess-one", classification=_mod.CLASS_POSSIBLE_CRASH,
        cwd="/repo/example-project", git_branch="feature-x", last_activity=1000.0,
        detail="only a transcript exists", entry_count=0, cwd_missing=False,
    )
    output = _mod.render_report(_blank_report(rows=[row]), redact=True)
    assert "sess-one" not in output
    assert "/repo/example-project" not in output
    assert "feature-x" not in output
    assert "session-1" in output
    assert "project-1" in output


def test_render_report_possible_crash_unredacted_preserves_real_values():
    row = _mod.SessionRow(
        session_id="sess-one", classification=_mod.CLASS_POSSIBLE_CRASH,
        cwd="/repo/example-project", git_branch="feature-x", last_activity=1000.0,
        detail="only a transcript exists", entry_count=0, cwd_missing=False,
    )
    output = _mod.render_report(_blank_report(rows=[row]), redact=False)
    assert "sess-one" in output
    assert "/repo/example-project" in output
    assert "feature-x" in output


def test_render_report_possible_crash_detail_with_custom_window_survives_redact():
    """The detail line's near-boot-window fragment is a value the user
    supplied on their own command line, not cwd/session/branch data — it
    renders unchanged under --redact rather than being stripped or mapped."""
    row = _mod.SessionRow(
        session_id="sess-one", classification=_mod.CLASS_POSSIBLE_CRASH,
        cwd="/repo/example-project", git_branch="feature-x", last_activity=1000.0,
        detail=(
            "only a transcript exists, with no registry or lock entry; its last activity sits "
            "within 72h before the last boot, but with no registry or lock corroboration this "
            "cannot confirm the session was still open at crash time."
        ),
        entry_count=0, cwd_missing=False,
    )
    output = _mod.render_report(_blank_report(rows=[row]), redact=True)
    assert "within 72h before the last boot" in output


def test_build_report_transcript_only_near_boot_surfaces_as_possible_crash(tmp_path):
    """End-to-end regression for the original bug shape: a real transcript
    file with no registry entry and no lock file, last activity inside the
    widened window before boot. Unit coverage of _near_boot_transcript_only_ids
    and _classify_session alone doesn't prove the
    _scan_transcripts -> known_session_ids -> classification wiring stays
    correct."""
    config_dir_path = tmp_path / "config"
    (config_dir_path / "sessions").mkdir(parents=True)
    session_id = "orphan-transcript"
    transcript_path = config_dir_path / "projects" / "any-project-dir-name" / f"{session_id}.jsonl"
    _write_transcript(transcript_path, [
        _meta_record(session_id), _cwd_record("/tmp/orphan-proj", session_id=session_id),
    ])
    boot_time = 1_700_000_000.0
    last_activity = boot_time - 2 * 3600  # 2h before boot: past the old 10min window, inside the new 4h one
    os.utime(transcript_path, (last_activity, last_activity))

    report = _mod.build_report(
        config_dirs=[config_dir_path], find_root=tmp_path / "home", boot_time_fn=lambda: boot_time,
    )
    row = next(r for r in report.rows if r.session_id == session_id)
    assert row.classification == _mod.CLASS_POSSIBLE_CRASH
    output = _mod.render_report(report, redact=False)
    assert "Possible crash — transcript only (1)" in output


def test_build_report_near_boot_window_seconds_widens_what_surfaces(tmp_path):
    """A transcript 3 days before boot is invisible at the default 4h window
    and surfaces only when the caller widens near_boot_window_seconds — the
    end-to-end path exercised by --near-boot-hours."""
    config_dir_path = tmp_path / "config"
    (config_dir_path / "sessions").mkdir(parents=True)
    session_id = "old-orphan-transcript"
    transcript_path = config_dir_path / "projects" / "any-project-dir-name" / f"{session_id}.jsonl"
    _write_transcript(transcript_path, [
        _meta_record(session_id), _cwd_record("/tmp/old-orphan-proj", session_id=session_id),
    ])
    boot_time = 1_700_000_000.0
    last_activity = boot_time - 3 * 86400  # 3 days before boot
    os.utime(transcript_path, (last_activity, last_activity))

    default_report = _mod.build_report(
        config_dirs=[config_dir_path], find_root=tmp_path / "home", boot_time_fn=lambda: boot_time,
    )
    assert session_id not in {row.session_id for row in default_report.rows}

    widened_report = _mod.build_report(
        config_dirs=[config_dir_path], find_root=tmp_path / "home", boot_time_fn=lambda: boot_time,
        near_boot_window_seconds=4 * 86400,
    )
    row = next(r for r in widened_report.rows if r.session_id == session_id)
    assert row.classification == _mod.CLASS_POSSIBLE_CRASH


# ---------------------------------------------------------------------------
# render_report — age annotation
# ---------------------------------------------------------------------------

def test_render_report_age_annotation_ordinary_boundaries():
    now = 1_000_000.0
    minutes_row = _mod.SessionRow(
        session_id="s-min", classification=_mod.CLASS_RESUMABLE,
        cwd="/tmp/p1", git_branch="main", last_activity=now - 45 * 60,
        detail="d", entry_count=1, cwd_missing=False,
    )
    hours_row = _mod.SessionRow(
        session_id="s-hr", classification=_mod.CLASS_RESUMABLE,
        cwd="/tmp/p2", git_branch="main", last_activity=now - 3 * 3600,
        detail="d", entry_count=1, cwd_missing=False,
    )
    days_row = _mod.SessionRow(
        session_id="s-day", classification=_mod.CLASS_RESUMABLE,
        cwd="/tmp/p3", git_branch="main", last_activity=now - 12 * 86400,
        detail="d", entry_count=1, cwd_missing=False,
    )
    output = _mod.render_report(_blank_report(rows=[minutes_row, hours_row, days_row]), redact=False, now=now)
    assert "45m old" in output
    assert "3h old" in output
    assert "12d old" in output


def test_render_report_age_annotation_omitted_when_now_not_supplied():
    row = _mod.SessionRow(
        session_id="s1", classification=_mod.CLASS_RESUMABLE,
        cwd="/tmp/p1", git_branch="main", last_activity=1000.0,
        detail="d", entry_count=1, cwd_missing=False,
    )
    output = _mod.render_report(_blank_report(rows=[row]), redact=False)
    assert "old" not in output


def test_render_report_age_annotation_omitted_for_unknown_last_activity():
    row = _mod.SessionRow(
        session_id="s1", classification=_mod.CLASS_RESUMABLE,
        cwd="/tmp/p1", git_branch="main", last_activity=None,
        detail="d", entry_count=1, cwd_missing=False,
    )
    output = _mod.render_report(_blank_report(rows=[row]), redact=False, now=1000.0)
    assert "old" not in output


def test_render_report_age_annotation_omitted_on_clock_skew():
    """last_activity postdating render_report's own now (clock skew between
    build_report's data collection and now's capture) must never render a
    negative duration — the age segment is omitted entirely instead."""
    row = _mod.SessionRow(
        session_id="s1", classification=_mod.CLASS_RESUMABLE,
        cwd="/tmp/p1", git_branch="main", last_activity=2000.0,
        detail="d", entry_count=1, cwd_missing=False,
    )
    output = _mod.render_report(_blank_report(rows=[row]), redact=False, now=1000.0)
    assert "old" not in output


# ---------------------------------------------------------------------------
# render_report — legacy bare-pid cleanup command
# ---------------------------------------------------------------------------

def test_render_report_legacy_pid_cleanup_command_quotes_multiple_hostile_paths():
    paths = [Path("/fake/sessions/111"), Path("/fake/sessions/222; rm -rf ~")]
    report = _blank_report(legacy_bare_pid_dead=paths)
    output = _mod.render_report(report, redact=False)
    rm_line = next(line for line in output.splitlines() if line.strip().startswith("rm --"))
    assert rm_line == "  rm -- /fake/sessions/111 '/fake/sessions/222; rm -rf ~'"


def test_render_report_legacy_pid_cleanup_command_absent_under_redact():
    paths = [Path("/fake/sessions/111"), Path("/fake/sessions/222")]
    report = _blank_report(legacy_bare_pid_dead=paths)
    output = _mod.render_report(report, redact=True)
    assert "rm --" not in output


def test_render_report_legacy_pid_paths_redacted_to_basename_under_declared_roots_default():
    """These paths span declared accounts (config_dirs_explicit=False, >1
    root) -- same account-directory-disclosure shape as the header line
    (Finding 1), so they must not print full paths, and the rm command
    (which needs full paths to be runnable) must be omitted rather than
    printed half-redacted."""
    paths = [Path("/fake/config/account-a/sessions/111"), Path("/fake/config/account-b/sessions/222")]
    report = _blank_report(
        config_dirs=[Path("/fake/config/account-a"), Path("/fake/config/account-b")],
        legacy_bare_pid_dead=paths,
    )
    output = _mod.render_report(report, redact=False, config_dirs_explicit=False)
    assert "111" in output and "222" in output
    assert "/fake/config/account-a" not in output
    assert "/fake/config/account-b" not in output
    assert "rm --" not in output


def test_render_report_legacy_pid_cleanup_command_present_under_explicit_config_dir():
    """An explicit --config-dir is a path the operator already typed --
    printing it back, and offering a runnable rm command, is the pre-PR
    behavior, preserved when config_dirs_explicit is True."""
    paths = [Path("/fake/config/account-a/sessions/111"), Path("/fake/config/account-b/sessions/222")]
    report = _blank_report(
        config_dirs=[Path("/fake/config/account-a"), Path("/fake/config/account-b")],
        legacy_bare_pid_dead=paths,
    )
    output = _mod.render_report(report, redact=False, config_dirs_explicit=True)
    assert "/fake/config/account-a/sessions/111" in output
    rm_line = next(line for line in output.splitlines() if line.strip().startswith("rm --"))
    assert "111" in rm_line and "222" in rm_line


# ---------------------------------------------------------------------------
# _declared_config_dirs()
# ---------------------------------------------------------------------------

def test_declared_config_dirs_returns_empty_list_when_roots_file_is_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(tmp_path / "does-not-exist"))
    assert _mod._declared_config_dirs() == []


def test_declared_config_dirs_accepts_sessions_only_root(monkeypatch, tmp_path):
    """The one behavior that must diverge from _config_dir.declared_transcript_roots():
    a root with sessions/ but no projects/ is valid here."""
    sessions_only = tmp_path / "sessions-only"
    (sessions_only / "sessions").mkdir(parents=True)
    roots_file = tmp_path / "roots"
    roots_file.write_text(f"{sessions_only}\n")
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
    assert _mod._declared_config_dirs() == [sessions_only]


def test_declared_config_dirs_accepts_projects_only_root(monkeypatch, tmp_path):
    projects_only = tmp_path / "projects-only"
    (projects_only / "projects").mkdir(parents=True)
    roots_file = tmp_path / "roots"
    roots_file.write_text(f"{projects_only}\n")
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
    assert _mod._declared_config_dirs() == [projects_only]


def test_declared_config_dirs_rejects_root_with_neither_subdir_index_only_warning(monkeypatch, tmp_path, capsys):
    bare_dir = tmp_path / "bare-account"
    bare_dir.mkdir()
    roots_file = tmp_path / "roots"
    roots_file.write_text(f"{bare_dir}\n")
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
    assert _mod._declared_config_dirs() == []
    err = capsys.readouterr().err
    assert "declared root 1" in err
    assert "post-crash-sessions" in err
    assert str(bare_dir) not in err


def test_declared_config_dirs_dedups_by_resolved_real_path(monkeypatch, tmp_path):
    root = tmp_path / "acct-dup"
    (root / "sessions").mkdir(parents=True)
    alias = tmp_path / "acct-dup-symlink"
    alias.symlink_to(root)
    roots_file = tmp_path / "roots"
    roots_file.write_text(f"{root}\n{alias}\n")
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
    assert _mod._declared_config_dirs() == [root]


# ---------------------------------------------------------------------------
# main() — CLI wiring, argument validation, end-to-end fixture corpus
# ---------------------------------------------------------------------------

def test_main_rejects_config_dir_without_sessions_or_projects_subdir(tmp_path, monkeypatch, capsys):
    bogus = tmp_path / "bogus"
    bogus.mkdir()
    monkeypatch.setenv(_mod._FIND_ROOT_ENV_VAR, str(tmp_path / "home"))
    exit_code = _mod.main(["--config-dir", str(bogus)])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert str(bogus) in captured.err


def test_main_rejects_nonexistent_config_dir(tmp_path, monkeypatch, capsys):
    missing = tmp_path / "does-not-exist"
    monkeypatch.setenv(_mod._FIND_ROOT_ENV_VAR, str(tmp_path / "home"))
    exit_code = _mod.main(["--config-dir", str(missing)])
    assert exit_code == 2


def test_main_rejects_zero_near_boot_hours(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(_mod._FIND_ROOT_ENV_VAR, str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "empty-config"))
    (tmp_path / "empty-config").mkdir()
    exit_code = _mod.main(["--near-boot-hours", "0"])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "--near-boot-hours" in captured.err


def test_main_rejects_negative_near_boot_hours(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(_mod._FIND_ROOT_ENV_VAR, str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "empty-config"))
    (tmp_path / "empty-config").mkdir()
    exit_code = _mod.main(["--near-boot-hours", "-1"])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "--near-boot-hours" in captured.err


def test_main_rejects_nan_near_boot_hours(tmp_path, monkeypatch, capsys):
    """A bare `<= 0` check lets `nan` through — NaN comparisons are always
    False in Python — which would silently disable near-boot detection with
    exit code 0 and no error. math.isfinite closes that gap."""
    monkeypatch.setenv(_mod._FIND_ROOT_ENV_VAR, str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "empty-config"))
    (tmp_path / "empty-config").mkdir()
    exit_code = _mod.main(["--near-boot-hours", "nan"])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "--near-boot-hours" in captured.err


def _spy_on_build_report(monkeypatch):
    """Monkeypatch build_report to capture its call kwargs instead of
    running the real scan -- the config_dirs kwarg it receives is main()'s
    fully-resolved list, so asserting on it directly is a stronger and more
    direct check than string-scanning render_report's printed output."""
    captured_kwargs = {}

    def fake_build_report(**kwargs):
        captured_kwargs.update(kwargs)
        return _blank_report()

    monkeypatch.setattr(_mod, "build_report", fake_build_report)
    return captured_kwargs


def _spy_on_render_report(monkeypatch):
    """Monkeypatch render_report to capture its call kwargs instead of
    formatting a real report -- pair with _spy_on_build_report so main()'s
    real registry/find scan never runs, keeping the wiring check hermetic."""
    captured_kwargs = {}

    def fake_render_report(report, **kwargs):
        captured_kwargs.update(kwargs)
        return "fake report"

    monkeypatch.setattr(_mod, "render_report", fake_render_report)
    return captured_kwargs


def test_main_always_scans_default_config_dir_first(monkeypatch, tmp_path):
    captured_kwargs = _spy_on_build_report(monkeypatch)
    default_dir = tmp_path / "default-config"
    default_dir.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(default_dir))
    monkeypatch.setenv(_mod._FIND_ROOT_ENV_VAR, str(tmp_path / "home"))
    exit_code = _mod.main([])
    assert exit_code == 0
    assert captured_kwargs["config_dirs"][0] == default_dir


def test_main_dedupes_default_config_dir_supplied_again_explicitly(monkeypatch, tmp_path):
    captured_kwargs = _spy_on_build_report(monkeypatch)
    default_dir = tmp_path / "default-config"
    (default_dir / "sessions").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(default_dir))
    monkeypatch.setenv(_mod._FIND_ROOT_ENV_VAR, str(tmp_path / "home"))
    exit_code = _mod.main(["--config-dir", str(default_dir)])
    assert exit_code == 0
    assert captured_kwargs["config_dirs"] == [default_dir]


def test_main_scans_declared_roots_by_default_when_no_config_dir_flag(tmp_path, monkeypatch):
    captured_kwargs = _spy_on_build_report(monkeypatch)
    default_dir = tmp_path / "default-config"
    default_dir.mkdir()
    declared_dir = tmp_path / "declared-config"
    (declared_dir / "projects").mkdir(parents=True)
    roots_file = tmp_path / "roots"
    roots_file.write_text(f"{declared_dir}\n")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(default_dir))
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
    monkeypatch.setenv(_mod._FIND_ROOT_ENV_VAR, str(tmp_path / "home"))

    exit_code = _mod.main([])
    assert exit_code == 0
    assert declared_dir in captured_kwargs["config_dirs"]
    assert len(captured_kwargs["config_dirs"]) == 2


def test_main_declared_sessions_only_root_is_not_silently_dropped(tmp_path, monkeypatch):
    """A declared root with a sessions/ dir but no projects/ dir is the
    crashed-fresh-account case this tool exists for -- declared_transcript_roots()'s
    own projects/-only requirement would drop it, so main() must apply its
    own looser sessions/-or-projects/ check instead."""
    captured_kwargs = _spy_on_build_report(monkeypatch)
    default_dir = tmp_path / "default-config"
    default_dir.mkdir()
    sessions_only_dir = tmp_path / "sessions-only-config"
    (sessions_only_dir / "sessions").mkdir(parents=True)
    roots_file = tmp_path / "roots"
    roots_file.write_text(f"{sessions_only_dir}\n")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(default_dir))
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
    monkeypatch.setenv(_mod._FIND_ROOT_ENV_VAR, str(tmp_path / "home"))

    exit_code = _mod.main([])
    assert exit_code == 0
    assert sessions_only_dir in captured_kwargs["config_dirs"]
    assert len(captured_kwargs["config_dirs"]) == 2


def test_main_explicit_config_dir_overrides_declared_roots_default(tmp_path, monkeypatch):
    """An explicit --config-dir takes precedence over the declared-roots
    default entirely, mirroring transcript-analysis.py's _resolve_scan_roots
    precedence -- the declared root must not also be scanned."""
    captured_kwargs = _spy_on_build_report(monkeypatch)
    default_dir = tmp_path / "default-config"
    default_dir.mkdir()
    declared_dir = tmp_path / "declared-config"
    (declared_dir / "projects").mkdir(parents=True)
    roots_file = tmp_path / "roots"
    roots_file.write_text(f"{declared_dir}\n")
    explicit_dir = tmp_path / "explicit-config"
    (explicit_dir / "sessions").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(default_dir))
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
    monkeypatch.setenv(_mod._FIND_ROOT_ENV_VAR, str(tmp_path / "home"))

    exit_code = _mod.main(["--config-dir", str(explicit_dir)])
    assert exit_code == 0
    assert captured_kwargs["config_dirs"] == [default_dir, explicit_dir]


def test_main_end_to_end_prints_resume_command_for_crashed_session(tmp_path, monkeypatch, capsys):
    """Drives main() against a tmp config dir holding a dead-pid registry
    entry, a dead-pid lock, and a matching transcript, asserting the full
    rendered report includes the resume command string."""
    config_dir_path = tmp_path / "config"
    sessions_dir = config_dir_path / "sessions"
    proj = tmp_path / "recoverable-project"
    proj.mkdir()
    dead = _dead_pid()
    session_id = "sess-ccc"
    _write_registry_entry(sessions_dir, dead, sessionId=session_id, cwd=str(proj))
    transcript_path = config_dir_path / "projects" / "any-project-dir-name" / f"{session_id}.jsonl"
    _write_transcript(transcript_path, [
        _meta_record(session_id), _cwd_record(str(proj), branch="main", session_id=session_id),
    ])
    # A dead-pid lock for a second, distinct crashed session with no transcript at all.
    # Placed under the injected find_root so the bounded `find` sweep (not the
    # cwd-harvest half, since no transcript ever mentions this cwd) discovers it.
    home_root = tmp_path / "home"
    lock_pid = _dead_pid()
    lock_proj = home_root / "lock-only-project"
    lock_session_id = "sess-ddd"
    _write_lock(lock_proj / ".claude" / "scheduled_tasks.lock", sessionId=lock_session_id, pid=lock_pid)

    far_past = 1000
    os.utime(sessions_dir / f"{dead}.json", (far_past, far_past))
    os.utime(transcript_path, (far_past, far_past))
    os.utime(lock_proj / ".claude" / "scheduled_tasks.lock", (far_past, far_past))

    empty_config = tmp_path / "empty-config"
    empty_config.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(empty_config))
    monkeypatch.setenv(_mod._FIND_ROOT_ENV_VAR, str(home_root))

    exit_code = _mod.main(["--config-dir", str(config_dir_path)])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert f"cd {proj} && claude --resume {session_id}" in captured.out
    assert "Resumable (1)" in captured.out
    assert f"session {lock_session_id}" in captured.out
    assert "Crashed, no transcript (1)" in captured.out
    # far_past (1970) proves main() actually wires now=time.time() into render_report —
    # the only production call site, never exercised by the render_report(..., now=...) unit tests above.
    assert re.search(r"\d+d old", captured.out)


def test_main_smoke_against_live_environment_no_traceback(tmp_path, monkeypatch, capsys):
    """No hardcoded numeric expectations — only that a real run completes
    cleanly. find_root is redirected to an empty tmp dir so the bounded
    `find` sweep never walks the real $HOME, and CLAUDE_CONFIG_DIR is pinned
    to an isolated tmp config dir by conftest.py's autouse fixture like every
    other test in this suite, so this run has no real config-dir state to
    read; it verifies only a clean, traceback-free exit."""
    monkeypatch.setenv(_mod._FIND_ROOT_ENV_VAR, str(tmp_path / "home"))
    exit_code = _mod.main([])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Traceback" not in captured.err
    assert "Post-crash session recovery report" in captured.out


def test_main_redact_flag_produces_ordinal_output(tmp_path, monkeypatch):
    """Proves --redact reaches render_report's redact kwarg, by spying on
    render_report itself -- the ordinal-substitution behavior that flag
    produces is already covered directly, hermetically, by
    test_render_report_redact_maps_cwd_and_session_to_ordinals_and_drops_branch.
    Also spies build_report so this wiring check never shells out to the
    real ps/find scan, matching test_main_threads_near_boot_hours_into_build_report's
    own hermeticity."""
    _spy_on_build_report(monkeypatch)
    captured_kwargs = _spy_on_render_report(monkeypatch)
    empty_config = tmp_path / "empty-config"
    empty_config.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(empty_config))
    monkeypatch.setenv(_mod._FIND_ROOT_ENV_VAR, str(tmp_path / "home"))

    exit_code = _mod.main(["--redact"])
    assert exit_code == 0
    assert captured_kwargs["redact"] is True


def test_main_redact_end_to_end_hides_session_id_and_cwd(tmp_path, monkeypatch, capsys):
    """Runs a real crashed session through the full classify -> build_report
    -> render_report chain under --redact -- the wiring test above only
    proves the flag reaches render_report, and render_report's own unit test
    (test_render_report_redact_maps_cwd_and_session_to_ordinals_and_drops_branch)
    only exercises a hand-built SessionRow's currently-known fields. This is
    the one test that would catch a future field carrying real session data
    into the report unredacted."""
    config_dir_path = tmp_path / "config"
    sessions_dir = config_dir_path / "sessions"
    proj = tmp_path / "recoverable-project"
    proj.mkdir()
    dead = _dead_pid()
    session_id = "sess-eee"
    _write_registry_entry(sessions_dir, dead, sessionId=session_id, cwd=str(proj))
    transcript_path = config_dir_path / "projects" / "any-project-dir-name" / f"{session_id}.jsonl"
    _write_transcript(transcript_path, [
        _meta_record(session_id), _cwd_record(str(proj), branch="main", session_id=session_id),
    ])
    far_past = 1000
    os.utime(sessions_dir / f"{dead}.json", (far_past, far_past))
    os.utime(transcript_path, (far_past, far_past))

    empty_config = tmp_path / "empty-config"
    empty_config.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(empty_config))
    monkeypatch.setenv(_mod._FIND_ROOT_ENV_VAR, str(tmp_path / "home"))

    exit_code = _mod.main(["--config-dir", str(config_dir_path), "--redact"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert session_id not in captured.out
    assert str(proj) not in captured.out
    assert "session-1" in captured.out
    assert "project-1" in captured.out


def test_main_threads_near_boot_hours_into_build_report(tmp_path, monkeypatch, capsys):
    """Proves the CLI-to-build_report wiring hermetically, by spying on
    build_report itself, rather than depending on the real system boot time
    (main() has no boot_time_fn injection seam, unlike build_report) — the
    actual windowing behavior is already covered, injectably, by
    test_build_report_near_boot_window_seconds_widens_what_surfaces."""
    captured_kwargs = {}

    def fake_build_report(**kwargs):
        captured_kwargs.update(kwargs)
        return _blank_report()

    monkeypatch.setattr(_mod, "build_report", fake_build_report)
    empty_config = tmp_path / "empty-config"
    empty_config.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(empty_config))
    monkeypatch.setenv(_mod._FIND_ROOT_ENV_VAR, str(tmp_path / "home"))

    exit_code = _mod.main(["--near-boot-hours", "72"])
    assert exit_code == 0
    assert captured_kwargs["near_boot_window_seconds"] == 72 * 3600.0


def test_main_threads_explicit_config_dir_flag_into_render_report(tmp_path, monkeypatch):
    """--config-dir passed explicitly must reach render_report's
    config_dirs_explicit kwarg as True -- the note-text consequence of that
    flag is covered directly and hermetically at the render_report layer by
    test_render_report_explicit_config_dir_note_overrides_populated_roots_file,
    not through main(). Also spies build_report so this wiring check never
    shells out to the real ps/find scan, matching
    test_main_threads_near_boot_hours_into_build_report's own hermeticity."""
    _spy_on_build_report(monkeypatch)
    captured_kwargs = _spy_on_render_report(monkeypatch)
    default_dir = tmp_path / "default-config"
    default_dir.mkdir()
    explicit_dir = tmp_path / "explicit-config"
    (explicit_dir / "sessions").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(default_dir))
    monkeypatch.setenv(_mod._FIND_ROOT_ENV_VAR, str(tmp_path / "home"))

    exit_code = _mod.main(["--config-dir", str(explicit_dir)])
    assert exit_code == 0
    assert captured_kwargs["config_dirs_explicit"] is True

