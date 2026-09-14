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

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from helpers import HOOKS_DIR

from .conftest import _dead_pid
from .test_lib_append_line_locked import (
    _EARLY_RETURN_CEILING_SECONDS,
    _LOCK_HOLD_SECONDS,
    _RETRY_BUDGET_SECONDS,
)

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


def _popen_append_json_line_locked(
    file: Path, lock_file: Path, line: str, dedup_filter: str
) -> subprocess.Popen:
    return subprocess.Popen(
        ["bash", "-c", f'. "{LIB_SH}"; _lib_append_json_line_locked "$1" "$2" "$3" "$4"',
         "_", str(file), str(lock_file), line, dedup_filter],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
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
        """A DEDUP_KEY_JQ_FILTER that passes the runtime shape guard but
        still isn't valid jq (a double comma) must not silently disable
        dedup nor block the append: the append still succeeds (fail open,
        same fallback as a genuine lock-race duplicate), but the failure is
        distinguishable on stderr from a jq call that actually resolved
        "not a duplicate"."""
        target = tmp_path / "state.jsonl"
        lock_file = tmp_path / "state.jsonl.lock"
        _append_json_line_locked(
            target, lock_file, '{"round":1,"disposition":"ADDRESS"}', "{round, disposition}",
        )

        result = _append_json_line_locked(
            target, lock_file, '{"round":2,"disposition":"ADDRESS"}', "{round,,disposition}",
        )

        assert result.returncode == 0, result.stderr
        assert target.read_text().splitlines() == [
            '{"round":1,"disposition":"ADDRESS"}',
            '{"round":2,"disposition":"ADDRESS"}',
        ], "the append must still succeed when the dedup check itself fails"
        assert "dedup check failed" in result.stderr

    def test_duplicate_with_quote_and_brace_bearing_value_still_dedups(self, tmp_path):
        """A dedup-relevant field value carrying an unbalanced quote and an
        unclosed brace must still round-trip through the dedup comparison's
        fromjson?/equality check once it's JSON-encoded into the line, so a
        retry carrying such a value still collapses to one line."""
        target = tmp_path / "state.jsonl"
        lock_file = tmp_path / "state.jsonl.lock"
        # Deliberately overlaps in character class with
        # test_review_ledger_script.py's
        # test_jq_filter_special_chars_in_finding_round_trip_unmodified
        # payload. That one probes CLI-level --arg round-tripping. This one
        # probes the dedup comparison's own round-trip. Don't collapse them
        # into one payload.
        breaking = 'unbalanced " quote and { unclosed brace'
        first_line = json.dumps({"round": 1, "finding": breaking})
        second_line = json.dumps({"round": 1, "finding": breaking, "rationale": "different text"})
        _append_json_line_locked(target, lock_file, first_line, "{round, finding}")

        result = _append_json_line_locked(target, lock_file, second_line, "{round, finding}")

        assert result.returncode == 0, result.stderr
        assert target.read_text().splitlines() == [first_line], (
            "a dedup-key value carrying an unbalanced quote and brace must still dedup"
        )

    def test_shipped_static_literal_still_works(self, tmp_path):
        """The exact literal review-ledger.sh ships -- a multi-field
        object-projection -- must still pass the runtime allowlist check
        unchanged."""
        target = tmp_path / "state.jsonl"
        lock_file = tmp_path / "state.jsonl.lock"
        shipped_filter = "{round, finding, disposition, rationale, source, authoring_agent, authoring_effort}"
        result = _append_json_line_locked(
            target, lock_file, '{"round":1,"disposition":"ADDRESS"}', shipped_filter,
        )
        assert result.returncode == 0, result.stderr
        assert target.read_text().splitlines() == ['{"round":1,"disposition":"ADDRESS"}']

    @pytest.mark.parametrize("unsafe_filter", ['{round, "$(whoami)"}', "{round, `id`}"])
    def test_filter_with_dollar_or_backtick_fails_open_without_reaching_jq(self, tmp_path, unsafe_filter):
        """A DEDUP_KEY_JQ_FILTER carrying a character outside a jq
        object-projection literal's charset must never be spliced into the
        jq program text. The append still succeeds (fail open, same
        fallback as any other malformed filter), but the guard's own
        stderr note distinguishes this from a jq call that actually ran."""
        target = tmp_path / "state.jsonl"
        lock_file = tmp_path / "state.jsonl.lock"
        result = _append_json_line_locked(
            target, lock_file, '{"round":1,"disposition":"ADDRESS"}', unsafe_filter,
        )
        assert result.returncode == 0, result.stderr
        assert target.read_text().splitlines() == ['{"round":1,"disposition":"ADDRESS"}']
        assert "is not a brace-delimited" in result.stderr

    @pytest.mark.parametrize("bad_char", ["\\", ";", "|", "'", "-", "."])
    def test_single_disallowed_character_fails_open_in_isolation(self, tmp_path, bad_char):
        """Each character is tested alone, not only bundled, so a future
        charset change is pinned per-character. Backslash matters most
        because the guard's `{`/`}` anchors sit outside any bracket
        expression."""
        target = tmp_path / "state.jsonl"
        lock_file = tmp_path / "state.jsonl.lock"
        unsafe_filter = "{round" + bad_char + "}"
        result = _append_json_line_locked(
            target, lock_file, '{"round":1,"disposition":"ADDRESS"}', unsafe_filter,
        )
        assert result.returncode == 0, result.stderr
        assert target.read_text().splitlines() == ['{"round":1,"disposition":"ADDRESS"}']

    @pytest.mark.parametrize("unsafe_filter", ["env", "now", "input"])
    def test_bare_jq_builtin_without_braces_fails_open_without_reaching_jq(self, tmp_path, unsafe_filter):
        """A charset-legal but brace-free identifier can be a real 0-arity
        jq builtin (e.g. `env` returns the process environment), not the
        inert field-shorthand the object-projection contract assumes. The
        guard rejects it by shape before it ever reaches jq, not only by
        charset."""
        target = tmp_path / "state.jsonl"
        lock_file = tmp_path / "state.jsonl.lock"
        result = _append_json_line_locked(
            target, lock_file, '{"round":1,"disposition":"ADDRESS"}', unsafe_filter,
        )
        assert result.returncode == 0, result.stderr
        assert target.read_text().splitlines() == ['{"round":1,"disposition":"ADDRESS"}']
        assert "is not a brace-delimited" in result.stderr

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

    def test_live_pid_lock_still_dedups_a_genuine_duplicate_and_emits_stderr_note(self, tmp_path, live_pid):
        """Retry exhaustion against a still-live holder must still run the
        dedup check, and a matching projection is a genuine duplicate
        regardless of lock state. Losing the lock only risks missing a
        concurrent duplicate, never falsely flagging one. The append also
        notes on stderr that it proceeded unlocked."""
        target = tmp_path / "state.jsonl"
        lock_file = tmp_path / "state.jsonl.lock"
        line = '{"round":1,"disposition":"ADDRESS"}'
        target.write_text(line + "\n")
        lock_file.write_text(str(live_pid))

        result = _append_json_line_locked(target, lock_file, line, "{round, disposition}")

        assert result.returncode == 0, result.stderr
        assert target.read_text().splitlines() == [line], "a genuine duplicate must still dedup unlocked"
        assert "_lib_acquire_append_lock: exhausted" in result.stderr, repr(result.stderr)

    def test_live_pid_lock_still_appends_a_non_duplicate_and_emits_stderr_note(self, tmp_path, live_pid):
        """The unlocked-append note is additive, not a change in dedup
        behavior: a non-duplicate candidate still lands even when the lock
        wasn't acquired."""
        target = tmp_path / "state.jsonl"
        lock_file = tmp_path / "state.jsonl.lock"
        existing = '{"round":1,"disposition":"ADDRESS"}'
        candidate = '{"round":2,"disposition":"CLEAN"}'
        target.write_text(existing + "\n")
        lock_file.write_text(str(live_pid))

        result = _append_json_line_locked(target, lock_file, candidate, "{round, disposition}")

        assert result.returncode == 0, result.stderr
        assert target.read_text().splitlines() == [existing, candidate]
        assert "_lib_acquire_append_lock: exhausted" in result.stderr, repr(result.stderr)

    def test_retry_exhaustion_does_not_abort_the_caller_under_set_e(self, tmp_path, live_pid):
        """_lib_acquire_append_lock's nonzero return on retry exhaustion
        must not trip a sourcing script's own `set -e` -- the guard
        _lib_append_json_line_locked wraps that call in. See
        test_lib_append_line_locked.py's own sibling test for why a
        pre-written, never-evicted live lock file is enough to force
        genuine retry exhaustion here."""
        target = tmp_path / "state.jsonl"
        lock_file = tmp_path / "state.jsonl.lock"
        lock_file.write_text(str(live_pid))
        result = subprocess.run(
            ["bash", "-c",
             f'set -euo pipefail; . "{LIB_SH}"; _lib_append_json_line_locked "$1" "$2" "$3" "$4"',
             "_", str(target), str(lock_file), '{"round":1,"disposition":"ADDRESS"}', "{round, disposition}"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert target.read_text().splitlines() == ['{"round":1,"disposition":"ADDRESS"}']


class TestLibAppendJsonLineLockedConcurrency:
    """Two-process race coverage of the shared _lib_acquire_append_lock
    primitive against this function's own, heavier critical section (an
    extra _lib_jq subprocess for the dedup-key comparison, on top of the
    grep + printf _lib_append_line_locked's own critical section runs).
    Mirrors test_lib_append_line_locked.py's
    TestLibAppendLineLockedConcurrency.test_lock_held_past_retry_budget_falls_through_to_unlocked_append."""

    def test_concurrent_appends_with_distinct_content_both_land(self, tmp_path):
        """Mirrors test_lib_append_line_locked.py's
        TestLibAppendLineLockedConcurrency.test_concurrent_appends_with_distinct_content_both_land:
        the winner's critical section completes and releases the lock well
        inside the loser's 0.25s retry budget, so the loser always wins on
        retry rather than exhausting it -- both distinct-dedup-key lines
        land."""
        target = tmp_path / "state.jsonl"
        lock_file = tmp_path / "state.jsonl.lock"
        line_a = '{"round":1,"disposition":"ADDRESS","finding":"line-a"}'
        line_b = '{"round":1,"disposition":"ADDRESS","finding":"line-b"}'
        procs = [
            _popen_append_json_line_locked(target, lock_file, line_a, "{finding}"),
            _popen_append_json_line_locked(target, lock_file, line_b, "{finding}"),
        ]
        for proc in procs:
            proc.communicate(timeout=10)

        assert set(target.read_text().splitlines()) == {line_a, line_b}

    @pytest.mark.timing
    def test_lock_held_past_retry_budget_falls_through_to_unlocked_append(self, tmp_path):
        """A lock held by a genuinely live holder for longer than the
        0.25s retry budget forces real retry exhaustion (not lucky
        reacquisition, not dead-holder eviction), and the append must still
        land via the unlocked-append fallback rather than blocking or
        dropping the write. The same bound holds even though this
        critical section additionally runs a jq subprocess for the
        dedup-key comparison."""
        target = tmp_path / "state.jsonl"
        lock_file = tmp_path / "state.jsonl.lock"
        # A pre-existing, non-matching line is required so the dedup branch's
        # `[ -f "$file" ] && [ -s "$file" ]` gate is true and the jq
        # subprocess actually runs on the timed path -- against an empty or
        # absent target, _lib_append_json_line_locked skips dedup entirely
        # and this test would measure the same path as its plain sibling.
        target.write_text('{"round":0,"disposition":"ADDRESS"}\n')
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
            result = _append_json_line_locked(
                target, lock_file, '{"round":1,"disposition":"ADDRESS"}',
                "{round, disposition}",
            )
            elapsed = time.monotonic() - start

            assert result.returncode == 0, result.stderr
            assert target.read_text().splitlines() == [
                '{"round":0,"disposition":"ADDRESS"}',
                '{"round":1,"disposition":"ADDRESS"}',
            ]
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

    def test_concurrent_appends_with_identical_content_dedup_to_one_line(self, tmp_path):
        """Two racing appends of the identical dedup key may dedup to one
        line (a low-consequence race outcome) but must never corrupt the
        file -- every resulting line must match exactly, with no partial or
        interleaved write. Mirrors test_lib_append_line_locked.py's own
        identical-content race test's hedge: a retrying caller can exhaust
        _LIB_APPEND_LOCK_RETRIES's 0.25s budget while the winner still holds
        the lock, and fall through to the unlocked-append fallback. If the
        winner hasn't written yet at that point, the fallen-through caller's
        `[ -f "$file" ]` existence guard evaluates false and skips the dedup
        check entirely rather than racing it, so two lines is a valid
        outcome alongside the deduped single line."""
        target = tmp_path / "state.jsonl"
        lock_file = tmp_path / "state.jsonl.lock"
        line = '{"round":1,"disposition":"ADDRESS"}'
        procs = [
            _popen_append_json_line_locked(target, lock_file, line, "{round, disposition}")
            for _ in range(2)
        ]
        for proc in procs:
            proc.communicate(timeout=10)

        lines = target.read_text().splitlines()
        assert lines in ([line], [line, line]), (
            f"expected 1 (deduped) or 2 (raced) identical lines, got: {lines}"
        )
