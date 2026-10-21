"""Tests for transcript_analysis/duration.py: two cmd_duration tests of the Bursts column and the span, idle,
and active split at the default gap, and three tests that recompute the idle-gap arithmetic inline without
calling cmd_duration.
"""
import time

import pytest
from transcript_analysis import duration

from .conftest import _asst, _table_cols, _write_jsonl


class TestDurationGapSplit:
    def test_large_gap_counted_as_idle(self):
        now = time.time()
        tss = sorted([now, now + 60, now + 120, now + 8000, now + 8060])
        gap_secs = 1800
        idle_gaps = [tss[i + 1] - tss[i] for i in range(len(tss) - 1) if tss[i + 1] - tss[i] > gap_secs]
        assert len(idle_gaps) == 1
        assert idle_gaps[0] == pytest.approx(8000 - 120, abs=1)

    def test_no_gaps_means_fully_active(self):
        now = time.time()
        tss = [now, now + 60, now + 120]
        idle_gaps = [tss[i + 1] - tss[i] for i in range(len(tss) - 1) if tss[i + 1] - tss[i] > 1800]
        assert idle_gaps == []

    def test_active_time_is_span_minus_idle(self):
        now = time.time()
        tss = sorted([now, now + 300, now + 10000, now + 10300])
        gap_secs = 1800
        span = tss[-1] - tss[0]
        idle_gaps = [tss[i + 1] - tss[i] for i in range(len(tss) - 1) if tss[i + 1] - tss[i] > gap_secs]
        idle = sum(idle_gaps)
        active = span - idle
        # gap between tss[1] and tss[2] = 9700s; active = 300 + 300 = 600s
        assert active == pytest.approx(600, abs=2)

    def test_cmd_duration_prints_bursts_header_and_single_burst_count(self, fake_projects, capsys):
        """cmd_duration labels its activity-burst column "Bursts" and counts
        one burst for timestamps with no idle gap between them."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:00:00.000Z"),
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:05:00.000Z"),
        ])
        args = type("A", (), {"projects": "*", "this_repo": False, "branches": None})()
        duration.cmd_duration(args)
        out = capsys.readouterr().out
        assert "Sessions" not in out
        cols = _table_cols(out, header_contains="Bursts", row_contains="feat")
        assert cols["Bursts"] == "1"

    def test_cmd_duration_counts_two_bursts_across_files_around_an_idle_gap(self, fake_projects, capsys):
        """One idle gap longer than --gap-minutes yields Bursts == 2 with
        Idle/Active split at that gap. The first file spans both bursts and two
        further files continue the second burst, so three files yield two
        bursts and a files-counted-as-bursts regression fails. File names sort
        in the reverse of timestamp order, so cmd_duration reads the later
        records first and a dropped per-branch timestamp sort fails."""
        _write_jsonl(fake_projects / "sess-z.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:00:00.000Z"),
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T10:05:00.000Z"),
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T12:00:00.000Z"),
        ])
        _write_jsonl(fake_projects / "sess-a.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T12:05:00.000Z"),
        ])
        _write_jsonl(fake_projects / "sess-b.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat", ts="2026-05-19T12:08:00.000Z"),
        ])
        args = type("A", (), {"projects": "*", "this_repo": False, "branches": None})()
        duration.cmd_duration(args)
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Bursts", row_contains="feat")
        assert cols["Bursts"] == "2"
        # 10:00 -> 12:08 is 128 min wide; the 10:05 -> 12:00 gap is 115 min idle.
        assert cols["Span(min)"] == "128"
        assert cols["Idle(min)"] == "115"
        assert cols["Active(min)"] == "13"
