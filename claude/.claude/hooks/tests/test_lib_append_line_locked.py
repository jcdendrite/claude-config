"""Unit tests for _lib.sh's _lib_append_line_locked, the shared
noclobber-lock / dead-PID-eviction / bounded-retry / dedup-then-append
primitive both review-ledger.sh and log-reviewer-round.sh call.

These call the function directly against a bare tmp_path file -- no git
repo, no JSON payload, no hook invocation -- mirroring
test_lib_reviewer_round_state.py's precedent for pinning a _lib.sh
function independent of either hook. Hook-level (subprocess-of-a-hook)
coverage of the same locking behavior lives in
test_log_reviewer_round.py's TestLogReviewerRoundConcurrency class
(test_stale_lock_held_by_dead_pid_is_evicted_and_append_proceeds,
test_live_pid_lock_falls_through_to_unlocked_append) and
test_review_ledger_script.py's TestReviewLedgerLocking class; those stay
as integration-level backstops, not replaced by this file.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from helpers import HOOKS_DIR

from .conftest import _dead_pid

LIB_SH = HOOKS_DIR / "_lib.sh"


def _append_line_locked(file: Path, lock_file: Path, line: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'. "{LIB_SH}"; _lib_append_line_locked "$1" "$2" "$3"',
         "_", str(file), str(lock_file), line],
        capture_output=True,
        text=True,
        check=False,
    )


class TestLibAppendLineLocked:
    def test_basic_append_creates_file_with_one_line(self, tmp_path):
        target = tmp_path / "state.txt"
        lock_file = tmp_path / "state.txt.lock"
        result = _append_line_locked(target, lock_file, "first-line")
        assert result.returncode == 0
        assert target.read_text().splitlines() == ["first-line"]

    def test_repeat_identical_line_is_deduped(self, tmp_path):
        target = tmp_path / "state.txt"
        lock_file = tmp_path / "state.txt.lock"
        _append_line_locked(target, lock_file, "same-line")
        result = _append_line_locked(target, lock_file, "same-line")
        assert result.returncode == 0
        assert target.read_text().splitlines() == ["same-line"]

    def test_stale_lock_held_by_dead_pid_is_evicted_and_append_proceeds(self, tmp_path):
        target = tmp_path / "state.txt"
        lock_file = tmp_path / "state.txt.lock"
        lock_file.write_text(str(_dead_pid()))
        result = _append_line_locked(target, lock_file, "after-dead-lock")
        assert result.returncode == 0
        assert target.read_text().splitlines() == ["after-dead-lock"]
        assert not lock_file.exists()

    def test_live_pid_lock_falls_through_to_unlocked_append(self, tmp_path, live_pid):
        target = tmp_path / "state.txt"
        lock_file = tmp_path / "state.txt.lock"
        lock_file.write_text(str(live_pid))
        result = _append_line_locked(target, lock_file, "after-live-lock")
        assert result.returncode == 0
        assert target.read_text().splitlines() == ["after-live-lock"]
