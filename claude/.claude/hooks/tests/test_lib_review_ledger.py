"""Unit tests for _lib.sh's _lib_acquire_append_lock and _ledger_sweep_window_days."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ._lib_sh_helpers import (
    _LIB_SH,
)


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
