"""Unit tests for _lib.sh's _lib_append_json_line_locked, the JSON-
projection sibling to _lib_append_line_locked that review-ledger.sh's own
append uses. Shares _lib_acquire_append_lock's lock-acquisition/eviction/
retry logic with _lib_append_line_locked (see test_lib_append_line_locked.py
for that shared half's own coverage); this file covers only the JSON
dedup-key projection _lib_append_line_locked doesn't have.

These call the function directly against a bare tmp_path file -- no git
repo, no session file, no JSON payload built via review-ledger.sh, no
subprocess of review-ledger.sh itself -- mirroring
test_lib_append_line_locked.py's own direct-call style. Integration-level
coverage of the same dedup behavior through review-ledger.sh's own CLI
lives in test_review_ledger_script.py.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from helpers import HOOKS_DIR

from .conftest import _dead_pid

LIB_SH = HOOKS_DIR / "_lib.sh"


def _append_json_line_locked(
    file: Path, lock_file: Path, line: str, dedup_filter: str
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'. "{LIB_SH}"; _lib_append_json_line_locked "$1" "$2" "$3" "$4"',
         "_", str(file), str(lock_file), line, dedup_filter],
        capture_output=True,
        text=True,
        check=False,
    )


class TestLibAppendJsonLineLocked:
    def test_basic_append_creates_file_with_one_line(self, tmp_path):
        target = tmp_path / "state.jsonl"
        lock_file = tmp_path / "state.jsonl.lock"
        result = _append_json_line_locked(
            target, lock_file, '{"round":1,"disposition":"ADDRESS"}', "{round, disposition}",
        )
        assert result.returncode == 0, result.stderr
        assert target.read_text().splitlines() == ['{"round":1,"disposition":"ADDRESS"}']

    def test_duplicate_per_dedup_key_projection_is_a_noop_but_touches_mtime(self, tmp_path):
        """Two lines whose dedup-key projection matches (same round,
        disposition) dedup even though their full text differs (a differing
        finding text) -- and the no-op still refreshes the file's own mtime,
        per _lib_append_line_locked's own dedup-no-op-is-still-activity
        rationale."""
        target = tmp_path / "state.jsonl"
        lock_file = tmp_path / "state.jsonl.lock"
        _append_json_line_locked(
            target, lock_file, '{"round":1,"disposition":"ADDRESS","finding":"first text"}',
            "{round, disposition}",
        )
        original_content = target.read_text()
        thirty_one_days_ago = time.time() - 31 * 24 * 60 * 60
        os.utime(target, (thirty_one_days_ago, thirty_one_days_ago))

        result = _append_json_line_locked(
            target, lock_file, '{"round":1,"disposition":"ADDRESS","finding":"different text"}',
            "{round, disposition}",
        )

        assert result.returncode == 0, result.stderr
        assert target.read_text() == original_content, "a dedup no-op must not change content"
        assert target.stat().st_mtime > thirty_one_days_ago, (
            "a dedup no-op must still refresh the file's own mtime"
        )

    def test_non_duplicate_per_dedup_key_projection_appends_a_second_line(self, tmp_path):
        """A differing round is a differing dedup-key projection, so it must
        land as a second line rather than dedup against the first."""
        target = tmp_path / "state.jsonl"
        lock_file = tmp_path / "state.jsonl.lock"
        _append_json_line_locked(
            target, lock_file, '{"round":1,"disposition":"ADDRESS"}', "{round, disposition}",
        )
        result = _append_json_line_locked(
            target, lock_file, '{"round":2,"disposition":"ADDRESS"}', "{round, disposition}",
        )
        assert result.returncode == 0, result.stderr
        assert target.read_text().splitlines() == [
            '{"round":1,"disposition":"ADDRESS"}',
            '{"round":2,"disposition":"ADDRESS"}',
        ]

    def test_duplicate_matches_a_later_line_not_just_the_first(self, tmp_path):
        """The dedup check scans every existing line, not only the first --
        a candidate matching the file's second line must still dedup."""
        target = tmp_path / "state.jsonl"
        lock_file = tmp_path / "state.jsonl.lock"
        _append_json_line_locked(
            target, lock_file, '{"round":1,"disposition":"ADDRESS"}', "{round, disposition}",
        )
        _append_json_line_locked(
            target, lock_file, '{"round":2,"disposition":"ADDRESS"}', "{round, disposition}",
        )
        original_content = target.read_text()

        result = _append_json_line_locked(
            target, lock_file, '{"round":2,"disposition":"ADDRESS","finding":"new text"}',
            "{round, disposition}",
        )

        assert result.returncode == 0, result.stderr
        assert target.read_text() == original_content, (
            "a candidate matching the second existing line's own dedup-key "
            "projection must dedup, not append a third line"
        )

    def test_malformed_dedup_filter_fails_open_and_logs_the_failure(self, tmp_path):
        """A DEDUP_KEY_JQ_FILTER that isn't valid jq must not silently
        disable dedup nor block the append: the append still succeeds (fail
        open, same fallback as a genuine lock-race duplicate), but the
        failure is distinguishable on stderr from a jq call that actually
        resolved "not a duplicate"."""
        target = tmp_path / "state.jsonl"
        lock_file = tmp_path / "state.jsonl.lock"
        _append_json_line_locked(
            target, lock_file, '{"round":1,"disposition":"ADDRESS"}', "{round, disposition}",
        )

        result = _append_json_line_locked(
            target, lock_file, '{"round":2,"disposition":"ADDRESS"}', "{round,",
        )

        assert result.returncode == 0, result.stderr
        assert target.read_text().splitlines() == [
            '{"round":1,"disposition":"ADDRESS"}',
            '{"round":2,"disposition":"ADDRESS"}',
        ], "the append must still succeed when the dedup check itself fails"
        assert "dedup check failed" in result.stderr

    def test_malformed_neighbor_line_does_not_blind_dedup_against_the_rest(self, tmp_path):
        """A non-JSON line anywhere in the file (e.g. a partial write from a
        crash) must not fail the whole dedup check -- a candidate matching a
        different, well-formed line must still dedup rather than silently
        re-duplicating forever once one line is corrupted."""
        target = tmp_path / "state.jsonl"
        lock_file = tmp_path / "state.jsonl.lock"
        target.write_text(
            "this is not json at all\n"
            '{"round":1,"disposition":"ADDRESS"}\n'
        )

        result = _append_json_line_locked(
            target, lock_file, '{"round":1,"disposition":"ADDRESS","finding":"new text"}',
            "{round, disposition}",
        )

        assert result.returncode == 0, result.stderr
        assert target.read_text().splitlines() == [
            "this is not json at all",
            '{"round":1,"disposition":"ADDRESS"}',
        ], "a duplicate of the valid line must not be appended despite the malformed neighbor"


class TestLibAppendJsonLineLockedLockEviction:
    """Dead-PID-eviction / live-PID-fallthrough coverage of the shared
    _lib_acquire_append_lock primitive, exercised through this function's
    own call path -- ported from test_lib_append_line_locked.py's
    TestLibAppendLineLocked, which exercises the same primitive through
    its sibling."""

    def test_stale_lock_held_by_dead_pid_is_evicted_and_append_proceeds(self, tmp_path):
        target = tmp_path / "state.jsonl"
        lock_file = tmp_path / "state.jsonl.lock"
        lock_file.write_text(str(_dead_pid()))
        result = _append_json_line_locked(
            target, lock_file, '{"round":1,"disposition":"ADDRESS"}', "{round, disposition}",
        )
        assert result.returncode == 0, result.stderr
        assert target.read_text().splitlines() == ['{"round":1,"disposition":"ADDRESS"}']
        assert not lock_file.exists()

    def test_live_pid_lock_falls_through_to_unlocked_append(self, tmp_path, live_pid):
        target = tmp_path / "state.jsonl"
        lock_file = tmp_path / "state.jsonl.lock"
        lock_file.write_text(str(live_pid))
        result = _append_json_line_locked(
            target, lock_file, '{"round":1,"disposition":"ADDRESS"}', "{round, disposition}",
        )
        assert result.returncode == 0, result.stderr
        assert target.read_text().splitlines() == ['{"round":1,"disposition":"ADDRESS"}']
        assert lock_file.exists()
