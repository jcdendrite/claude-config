"""Unit tests for _lib.sh's _lib_append_line_locked, the
noclobber-lock / dead-PID-eviction / bounded-retry / whole-line-dedup-then-
append primitive log-reviewer-round.sh calls. review-ledger.sh's own
append uses the JSON-projection sibling, _lib_append_json_line_locked,
instead -- see test_review_ledger_script.py's TestReviewLedgerRoundScopedDedup
class for that primitive's own dedup-key coverage. Both share
_lib_acquire_append_lock's lock-acquisition/eviction/retry logic, exercised
here only through this function's own call path.

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
import time
from pathlib import Path

import pytest
from helpers import HOOKS_DIR

from .conftest import _dead_pid

LIB_SH = HOOKS_DIR / "_lib.sh"

# Mirrors _lib_acquire_append_lock's own _LIB_APPEND_LOCK_RETRIES=5 x its
# hardcoded 0.05s per-attempt sleep (_lib.sh has no named constant for the
# sleep itself). A holder that outlives this by a wide margin forces a
# racing call to genuinely exhaust every retry rather than reacquire on a
# lucky iteration.
_RETRY_BUDGET_SECONDS = 5 * 0.05
# Comfortably longer than _RETRY_BUDGET_SECONDS so the holder is still live
# throughout the racing call's entire retry loop.
_LOCK_HOLD_SECONDS = 3
# Upper bound for the racing call's own elapsed time: well above the
# ~0.25s retry budget plus subprocess/bash-source overhead, but well below
# _LOCK_HOLD_SECONDS, so a call that instead blocked until the holder
# released is unambiguously distinguishable from genuine retry exhaustion.
_EARLY_RETURN_CEILING_SECONDS = 1.5


def _append_line_locked(file: Path, lock_file: Path, line: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'. "{LIB_SH}"; _lib_append_line_locked "$1" "$2" "$3"',
         "_", str(file), str(lock_file), line],
        capture_output=True,
        text=True,
        check=False,
    )


def _popen_append_line_locked(file: Path, lock_file: Path, line: str) -> subprocess.Popen:
    return subprocess.Popen(
        ["bash", "-c", f'. "{LIB_SH}"; _lib_append_line_locked "$1" "$2" "$3"',
         "_", str(file), str(lock_file), line],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
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
        assert lock_file.exists()


class TestLibAppendLineLockedConcurrency:
    """Two-process race coverage of the shared _lib_acquire_append_lock
    primitive (acquisition/eviction/retry), exercised here through this
    function's own call path against a bare tmp_path file -- no CLI, no
    git repo. Mirrors test_review_ledger_script.py's TestReviewLedgerLocking
    race tests, which exercise the same primitive through review-ledger.sh's
    CLI and _lib_append_json_line_locked instead."""

    def test_concurrent_appends_with_distinct_content_both_land(self, tmp_path):
        """Two real racing processes never actually exhaust
        _LIB_APPEND_LOCK_RETRIES here: the winner's critical section (a
        grep + printf, no jq) completes and releases the lock well inside
        the loser's 0.25s retry budget, so the loser always wins on retry.
        This test therefore verifies both lines land under best-effort
        contention, not the retry-exhaustion -> unlocked-append fallback --
        see test_lock_held_past_retry_budget_falls_through_to_unlocked_append
        below for that."""
        target = tmp_path / "state.txt"
        lock_file = tmp_path / "state.txt.lock"
        procs = [
            _popen_append_line_locked(target, lock_file, "line-a"),
            _popen_append_line_locked(target, lock_file, "line-b"),
        ]
        for proc in procs:
            proc.communicate(timeout=10)

        assert set(target.read_text().splitlines()) == {"line-a", "line-b"}

    @pytest.mark.timing
    def test_lock_held_past_retry_budget_falls_through_to_unlocked_append(self, tmp_path):
        """A lock held by a genuinely live holder for longer than the
        0.25s retry budget forces real retry exhaustion (not lucky
        reacquisition, not dead-holder eviction), and the append must still
        land via the unlocked-append fallback rather than blocking or
        dropping the write."""
        target = tmp_path / "state.txt"
        lock_file = tmp_path / "state.txt.lock"
        # The holder writes its own live pid into the lock file itself (the
        # same noclobber idiom _lib_acquire_append_lock uses) and then holds
        # it for _LOCK_HOLD_SECONDS, well past the 0.25s retry budget --
        # long enough that eviction never fires and the racing append must
        # exhaust every retry against a still-live holder.
        holder = subprocess.Popen(
            ["bash", "-c", f'echo "$$" > "$1"; sleep {_LOCK_HOLD_SECONDS}; rm -f "$1"',
             "_", str(lock_file)],
        )
        try:
            deadline = time.monotonic() + 5
            while not lock_file.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert lock_file.exists(), "holder failed to create the lock file in time"
            holder_pid = lock_file.read_text().strip()

            start = time.monotonic()
            result = _append_line_locked(target, lock_file, "after-exhausted-retries")
            elapsed = time.monotonic() - start

            assert result.returncode == 0
            assert target.read_text().splitlines() == ["after-exhausted-retries"]
            # Genuine exhaustion takes at least the retry budget (each
            # `sleep 0.05` is a guaranteed minimum, never less) but nowhere
            # near the holder's own hold duration -- an early return would
            # mean it never really exhausted its retries against the
            # still-live holder.
            assert _RETRY_BUDGET_SECONDS <= elapsed < _EARLY_RETURN_CEILING_SECONDS, (
                f"append call took {elapsed:.2f}s -- expected roughly the "
                f"{_RETRY_BUDGET_SECONDS}s retry budget, not an early return "
                f"or a block until the {_LOCK_HOLD_SECONDS}s holder released"
            )
            assert lock_file.read_text().strip() == holder_pid, (
                "the still-live holder's lock must be left alone by the "
                "fallback append, not evicted or overwritten"
            )
        finally:
            try:
                holder.wait(timeout=_LOCK_HOLD_SECONDS + 5)
            except subprocess.TimeoutExpired:
                holder.kill()
                holder.wait()

    def test_concurrent_appends_with_identical_content_produce_no_corruption(self, tmp_path):
        """Two racing appends of the identical line may dedup to one line
        (a low-consequence race outcome) but must never corrupt the file --
        every resulting line must match exactly, with no partial or
        interleaved write."""
        target = tmp_path / "state.txt"
        lock_file = tmp_path / "state.txt.lock"
        procs = [_popen_append_line_locked(target, lock_file, "same-line") for _ in range(2)]
        for proc in procs:
            proc.communicate(timeout=10)

        lines = target.read_text().splitlines()
        assert lines in (["same-line"], ["same-line", "same-line"]), (
            f"expected 1 (deduped) or 2 (raced) identical lines, got: {lines}"
        )
