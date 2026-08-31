"""Tests for nudge-long-turn-subagent.sh. See the hook's own header comment
for what it does and why; see docs/design-decisions.md §33 for the
threshold's measurement basis.

All tests sandbox $HOME via monkeypatch so markers and logs land in tmp_path
rather than the real $HOME.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest
from helpers import (
    CANARY_CONTENT,
    HOOKS_DIR,
    TRAVERSAL_SESSION_ID,
    build_path_without,
    plant_traversal_canary,
)

NUDGE_HOOK = HOOKS_DIR / "nudge-long-turn-subagent.sh"

SESSION_ID = "test-session-long-turn-001"

# Mirrors the hook's own shipped defaults so no test hand-computes them.
DEFAULT_THRESHOLD = 340
DEFAULT_SAMPLE_CADENCE = 10

# The five malformed shapes resolve_threshold/resolve_sample_cadence guard
# against: empty, a literal zero, non-digit, zero-padded, and 9+ digits
# (which risks wrapping negative in bash's signed 64-bit arithmetic).
MALFORMED_GUARD_VALUES = ["", "0", "not-a-number", "0340", "999999999"]

# A small LONG_TURN_NUDGE_MAX_SCAN_WINDOW_BYTES override lets the FSM tests
# below pin the same branch logic against KB-scale fixtures instead of the
# production default's multi-MB windows.
SMALL_SCAN_WINDOW_BYTES = 4000


def _assistant_turn_record() -> dict:
    """A minimal transcript record shaped like the hook's own turn-counting
    filter selects on: `.message? and .message.usage`. Field values are
    irrelevant -- only the shape matters for counting."""
    return {"type": "assistant", "message": {"role": "assistant", "usage": {"output_tokens": 1}}}


def _write_transcript(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def _append_to_transcript(path: Path, records: list[dict]) -> None:
    """Append records to an already-written transcript, matching how a real
    Claude Code transcript only ever grows. Required once any fire has
    happened: the incremental-read cache advances a byte offset past what it
    already read, so a same-or-larger rewrite is indistinguishable from real
    growth only if the file actually grows."""
    with path.open("a") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")


def _run_hook(payload: dict, tmp_path: Path, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(tmp_path)}
    env.pop("CLAUDE_CONFIG_DIR", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(NUDGE_HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _base_payload(
    transcript_path: Path,
    session_id: str = SESSION_ID,
    agent_type: str = "general-purpose",
    hook_event_name: str = "PostToolBatch",
) -> dict:
    return {
        "session_id": session_id,
        "agent_type": agent_type,
        "transcript_path": str(transcript_path),
        "hook_event_name": hook_event_name,
    }


def _fire(
    transcript: Path, tmp_path: Path, times: int, *, session_id: str = SESSION_ID,
    agent_type: str = "general-purpose", extra_env: dict | None = None,
) -> list[subprocess.CompletedProcess]:
    """Fire the hook `times` times against a fixed (non-growing) transcript,
    the common shape for exercising cadence and threshold logic without a
    transcript that changes mid-sequence."""
    payload = _base_payload(transcript, session_id=session_id, agent_type=agent_type)
    return [_run_hook(payload, tmp_path, extra_env=extra_env) for _ in range(times)]


def _marker_dir(tmp_path: Path) -> Path:
    return tmp_path / ".claude" / ".long-turn-nudge-fired.d"


def _fired_marker_path(tmp_path: Path, session_id: str = SESSION_ID) -> Path:
    return _marker_dir(tmp_path) / session_id


def _scan_state_path(tmp_path: Path, session_id: str = SESSION_ID) -> Path:
    return _marker_dir(tmp_path) / f"{session_id}-scan"


def _invocations_marker_path(tmp_path: Path, session_id: str = SESSION_ID) -> Path:
    return _marker_dir(tmp_path) / f"{session_id}-invocations"


def _log_path(tmp_path: Path) -> Path:
    return tmp_path / ".claude" / ".long-turn-nudge.log"


class TestNudgeLongTurnSubagent:
    # -------------------------------------------------------------------
    # Subagent-only gate (inverted polarity from the handoff-nudge hook)
    # -------------------------------------------------------------------

    def test_fires_for_a_subagent_dispatch_above_threshold(self, tmp_path):
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(DEFAULT_THRESHOLD + 5)])
        results = _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        assert all(r.returncode == 0 for r in results)
        assert results[-1].stdout.strip() != "", "must fire on the threshold-checked fire"

    def test_never_fires_in_the_main_session_even_far_above_threshold(self, tmp_path):
        """agent_type empty (main session) must never nudge, even when the
        transcript is far past the turn-count threshold -- the opposite of
        nudge-handoff-near-context-cap.sh's own subagent gate."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(2_500)])
        results = _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE * 3, agent_type="")
        assert all(r.returncode == 0 for r in results)
        assert all(r.stdout.strip() == "" for r in results)
        assert all(r.stderr.strip() == "" for r in results)
        assert not _marker_dir(tmp_path).exists(), "main session must leave no state at all"

    def test_fires_for_any_recognized_subagent_type_not_only_general_purpose(self, tmp_path):
        for agent_type in ("code-writer", "staff-backend-engineer", "Explore", "ciso-reviewer"):
            transcript = tmp_path / f"t-{agent_type}.jsonl"
            _write_transcript(transcript, [_assistant_turn_record() for _ in range(DEFAULT_THRESHOLD + 1)])
            results = _fire(
                transcript, tmp_path, DEFAULT_SAMPLE_CADENCE,
                session_id=f"session-{agent_type}", agent_type=agent_type,
            )
            assert results[-1].stdout.strip() != "", f"must fire for agent_type={agent_type}"

    # -------------------------------------------------------------------
    # Threshold boundary
    # -------------------------------------------------------------------

    def test_silent_below_threshold(self, tmp_path):
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(DEFAULT_THRESHOLD - 1)])
        results = _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        assert all(r.returncode == 0 for r in results)
        assert all(r.stdout.strip() == "" for r in results)

    def test_fires_exactly_at_threshold(self, tmp_path):
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(DEFAULT_THRESHOLD)])
        results = _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        assert results[-1].stdout.strip() != "", "threshold is a >= comparison, not strictly greater-than"

    def test_threshold_env_override_respected(self, tmp_path):
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(50)])
        results = _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE, extra_env={"LONG_TURN_NUDGE_THRESHOLD": "40"})
        assert results[-1].stdout.strip() != "", "a lowered threshold must fire on a smaller transcript"

    @pytest.mark.parametrize("malformed_value", MALFORMED_GUARD_VALUES)
    def test_malformed_threshold_override_falls_back_to_default(self, tmp_path, malformed_value):
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(DEFAULT_THRESHOLD - 1)])
        results = _fire(
            transcript, tmp_path, DEFAULT_SAMPLE_CADENCE,
            extra_env={"LONG_TURN_NUDGE_THRESHOLD": malformed_value},
        )
        assert all(r.stdout.strip() == "" for r in results), (
            f"malformed override {malformed_value!r} must fall back to the shipped default, not degrade toward 0"
        )

    # -------------------------------------------------------------------
    # Sampled firing cadence
    # -------------------------------------------------------------------

    def test_no_scan_or_output_before_the_nth_fire(self, tmp_path):
        """A transcript already far past threshold must still stay silent,
        and must not even create the scan-state file, for every fire before
        the cadence divisor is reached."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(1_000)])
        results = _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE - 1)
        assert all(r.stdout.strip() == "" for r in results)
        assert not _scan_state_path(tmp_path).exists(), "no incremental scan must run before the sampled fire"
        invocation_count = len(_invocations_marker_path(tmp_path).read_text(encoding="utf-8"))
        assert invocation_count == DEFAULT_SAMPLE_CADENCE - 1

    def test_scan_and_output_run_on_the_nth_fire(self, tmp_path):
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(1_000)])
        results = _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        assert results[-1].stdout.strip() != ""
        assert _scan_state_path(tmp_path).exists()

    def test_cadence_env_override_respected(self, tmp_path):
        """A cadence of 3 checks on the 3rd fire, not the shipped default's 10th."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(1_000)])
        results = _fire(transcript, tmp_path, 3, extra_env={"LONG_TURN_NUDGE_SAMPLE_CADENCE": "3"})
        assert results[0].stdout.strip() == ""
        assert results[1].stdout.strip() == ""
        assert results[2].stdout.strip() != "", "the 3rd fire is the sampled one under cadence=3"

    @pytest.mark.parametrize("malformed_value", MALFORMED_GUARD_VALUES)
    def test_malformed_cadence_override_falls_back_to_default(self, tmp_path, malformed_value):
        """A cadence that degrades to 0 would divide by zero on every fire;
        the malformed-value guard must keep the shipped default instead."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(1_000)])
        results = _fire(
            transcript, tmp_path, DEFAULT_SAMPLE_CADENCE,
            extra_env={"LONG_TURN_NUDGE_SAMPLE_CADENCE": malformed_value},
        )
        assert all(r.returncode == 0 for r in results)
        assert results[-1].stdout.strip() != "", (
            f"cadence override {malformed_value!r} must fall back to the default divisor, not crash silently"
        )

    # -------------------------------------------------------------------
    # Fires at most once per dispatch
    # -------------------------------------------------------------------

    def test_does_not_re_fire_after_the_first_nudge(self, tmp_path):
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(DEFAULT_THRESHOLD + 5)])
        first_pass = _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        assert first_pass[-1].stdout.strip() != ""
        assert _fired_marker_path(tmp_path).exists()

        _append_to_transcript(transcript, [_assistant_turn_record() for _ in range(200)])
        second_pass = _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        assert all(r.stdout.strip() == "" for r in second_pass), "a dispatch gets at most one nudge"

    # -------------------------------------------------------------------
    # Concurrent fires: SCAN_STATE_FILE and FIRED_MARKER races
    # -------------------------------------------------------------------

    def test_concurrent_mkdir_claims_exactly_one_winner(self, tmp_path):
        """Exercises the exact `mkdir DIR` atomic-claim idiom FIRED_MARKER
        uses directly, decoupled from the hook's own cadence-sampling and
        turn-count gates. N processes race to mkdir the same path, spawned
        via subprocess.Popen (non-blocking, so process-creation overhead
        alone produces genuine overlapping execution windows -- no
        artificial synchronization needed). POSIX mkdir(2) is atomic, so
        exactly one of the N processes must see it succeed -- the property
        test_concurrent_fires_do_not_double_nudge below cannot itself
        prove, since the FIRED_MARKER race it needs is gated behind an
        unrelated, uncontrolled race (both fires must land on the same
        sampled-cadence boundary together). Mirrors
        test_atomic_append_no_lost_writes_under_concurrency in
        test_nudge_handoff_near_context_cap.py."""
        target = tmp_path / "concurrent-mkdir-target"
        n = 20
        procs = [subprocess.Popen(["mkdir", str(target)], stderr=subprocess.DEVNULL) for _ in range(n)]
        exit_codes = [p.wait() for p in procs]
        assert sum(1 for code in exit_codes if code == 0) == 1
        assert target.is_dir()

    @pytest.mark.timing
    def test_concurrent_fires_do_not_double_nudge(self, tmp_path):
        """Two near-simultaneous PostToolBatch fires, primed to land on the
        sampled-cadence boundary together: a smoke check that concurrent
        invocation neither crashes nor emits more than one nudge -- see
        test_concurrent_mkdir_claims_exactly_one_winner above for why this
        alone can't prove the mkdir claim is atomic. Mirrors
        test_escalation_counter_concurrent_rearms_no_lost_update in
        test_nudge_handoff_near_context_cap.py. Marked timing (run
        serially, -m timing -n0) for the same reason that test is: heavier
        xdist parallel load skews which interleaving is likelier.

        Each fire's own `wc -c` read of the invocations counter happens in
        a separate subprocess after its own atomic append, so both fires
        can observe the same post-both-appends count and both cross the
        sampled-cadence boundary -- but are not guaranteed to. What must
        never happen is more than one fire winning the FIRED_MARKER claim."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(DEFAULT_THRESHOLD + 5)])
        _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE - 2)

        results: list[subprocess.CompletedProcess | None] = [None, None]

        def _run(i: int) -> None:
            results[i] = _run_hook(_base_payload(transcript), tmp_path)

        threads = [threading.Thread(target=_run, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(r.returncode == 0 for r in results)
        nonempty_count = sum(1 for r in results if r.stdout.strip() != "")
        assert nonempty_count <= 1, (
            f"at most one of two racing fires may win the FIRED_MARKER claim, got {nonempty_count}"
        )

        if nonempty_count > 0:
            assert _fired_marker_path(tmp_path).exists(), "a winning fire must have crossed the threshold"
            # Known gap: SCAN_STATE_FILE's read-modify-write has no atomicity
            # guarantee under concurrent fires, so this only asserts the file
            # isn't corrupted, not that the running total reflects every turn.
            offset, total, _ = _scan_state_path(tmp_path).read_text().splitlines()
            assert int(total) >= 0
            assert int(offset) >= 0

    def test_scan_lock_held_by_another_fire_leaves_scan_state_unchanged(self, tmp_path):
        """A same-session fire that can't acquire _scan_turn_count_cached's
        exclusive lock -- another fire already holds it -- must skip its own
        scan contribution entirely rather than reading or writing state,
        proving the lock's rejection path specifically rather than merely
        eventual convergence. Simulated deterministically by pre-creating
        the lock directory the hook itself would create, rather than racing
        two real processes whose timing may or may not land as intended."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(50)])
        _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        scan_state = _scan_state_path(tmp_path)
        offset_before, total_before, _ = scan_state.read_text().splitlines()
        assert int(total_before) == 50

        _append_to_transcript(transcript, [_assistant_turn_record() for _ in range(20)])

        lock_dir = scan_state.parent / f"{scan_state.name}.lock"
        lock_dir.mkdir()
        try:
            results = _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
            assert all(r.returncode == 0 for r in results), "a failed lock acquisition must still fail open"
        finally:
            lock_dir.rmdir()

        offset_after, total_after, _ = scan_state.read_text().splitlines()
        assert offset_after == offset_before, (
            "a fire that lost the lock race must not touch SCAN_STATE_FILE at all"
        )
        assert total_after == total_before

    def test_lock_miss_racing_a_shrink_leaves_the_stale_state_for_the_next_fire_to_reset_correctly(self, tmp_path):
        """A fire that loses the lock race (simulated deterministically, per
        test_scan_lock_held_by_another_fire_leaves_scan_state_unchanged
        above) while the transcript has also shrunk since the last stored
        offset must leave the stale, pre-shrink state untouched -- same as
        losing the lock race against an unchanged transcript. The next fire
        to actually acquire the lock must then perform the shrink-reset
        exactly as test_transcript_shrink_resets_offset_and_total_instead_of_compounding
        checks in isolation, not a compounded or skipped reset carried over
        from the fire that lost the race."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(200)])
        _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        scan_state = _scan_state_path(tmp_path)
        offset_before, total_before, _ = scan_state.read_text().splitlines()
        assert int(total_before) == 200

        _write_transcript(transcript, [_assistant_turn_record() for _ in range(30)])
        assert transcript.stat().st_size < int(offset_before), "the replacement must actually be smaller"

        lock_dir = scan_state.parent / f"{scan_state.name}.lock"
        lock_dir.mkdir()
        try:
            results = _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
            assert all(r.returncode == 0 for r in results), "a failed lock acquisition must still fail open"
        finally:
            lock_dir.rmdir()

        offset_after_loss, total_after_loss, _ = scan_state.read_text().splitlines()
        assert offset_after_loss == offset_before, (
            "a fire that lost the lock race must not touch SCAN_STATE_FILE, even with a shrunk transcript pending"
        )
        assert total_after_loss == total_before

        _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        offset_final, total_final, misaligned_final = scan_state.read_text().splitlines()
        assert int(total_final) == 30, (
            "the next fire to win the lock must reset to the shrunk file's own turn count, "
            "not compound onto the stale pre-shrink total the losing fire preserved"
        )
        assert int(offset_final) == transcript.stat().st_size
        assert misaligned_final == "0"

    def test_two_concurrent_fires_racing_for_the_scan_lock_leave_scan_state_internally_consistent(self, tmp_path):
        """Two real concurrent hook fires race for _scan_turn_count_cached's
        mkdir lock under real OS scheduling. LONG_TURN_NUDGE_SAMPLE_CADENCE=1
        makes every fire attempt the lock, not just every 10th.
        Process-creation overhead alone produces the overlapping execution
        windows this needs -- the same mechanics test_concurrent_mkdir_claims_exactly_one_winner
        above relies on -- so no sleep or stub tricks are required. The
        transcript grows between the two racers' starts (not held static),
        so this exercises a real read against a transcript that changed
        mid-race rather than one both racers see identically throughout.
        This alone does not prove the lock's exclusivity: the append lands
        before both racers' reads reliably enough (verified empirically)
        that a loosened mkdir -> mkdir -p still converges on the same
        correct total here.
        test_scan_lock_held_by_another_fire_leaves_scan_state_unchanged
        is the test that actually fails under a loosened mkdir -> mkdir -p
        (verified empirically), since it pins the lock loser's own
        SCAN_STATE_FILE untouched rather than the racers' converged total."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(50)])
        extra_env = {"LONG_TURN_NUDGE_SAMPLE_CADENCE": "1"}

        results: list[subprocess.CompletedProcess | None] = [None, None]

        def _run(i: int) -> None:
            results[i] = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)

        threads = [threading.Thread(target=_run, args=(i,)) for i in range(2)]
        threads[0].start()
        _append_to_transcript(transcript, [_assistant_turn_record() for _ in range(30)])
        threads[1].start()
        for t in threads:
            t.join()

        assert all(r is not None and r.returncode == 0 for r in results), (
            "a failed lock acquisition must still fail open"
        )

        lines = _scan_state_path(tmp_path).read_text().splitlines()
        assert len(lines) == 3, "the winner's write must land whole, not torn or interleaved with the loser's"
        offset, total, misaligned = lines
        assert offset.isdigit() and int(offset) == transcript.stat().st_size, (
            "the state on disk must reflect one full, uncorrupted scan of the grown transcript"
        )
        assert total.isdigit() and int(total) == 80
        assert misaligned == "0"

    @pytest.mark.timing
    def test_scan_lock_release_point_matches_the_read_scan_write_scope(self, tmp_path):
        """Stubs jq to sleep only on the `-n` invocation -- the
        output-JSON build, which runs strictly after
        _scan_turn_count_cached's own read-scan-write sequence has already
        returned -- so a raw `mkdir` probe on the scan lock directory,
        attempted while that stall is in progress, directly observes
        whether the lock is still held at that point. A raw mkdir probe
        (mirrors test_concurrent_mkdir_claims_exactly_one_winner's own
        primitive-level approach) isolates the scan lock's occupancy from
        the unrelated FIRED_MARKER and threshold-check machinery a second
        real hook fire would also exercise. The hook's own explicit
        release on every return path scopes the lock to the read-scan-
        write sequence itself, not the rest of the script through exit,
        so the probe here is expected to succeed."""
        if not shutil.which("timeout"):
            pytest.skip("timeout(1) not available — BSD/macOS without coreutils")
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(DEFAULT_THRESHOLD + 5)])

        real_jq = shutil.which("jq")
        stub_dir = tmp_path / "stub-bin"
        stub_dir.mkdir()
        stub = stub_dir / "jq"
        stub.write_text(
            '#!/bin/bash\n'
            'if [ "$1" = "-n" ]; then\n'
            '  sleep 5\n'
            'fi\n'
            f'exec {real_jq} "$@"\n'
        )
        stub.chmod(0o755)
        extra_env = {"PATH": f"{stub_dir}:{os.environ['PATH']}", "LONG_TURN_NUDGE_SAMPLE_CADENCE": "1"}

        scan_state = _scan_state_path(tmp_path)
        result_holder: dict[str, subprocess.CompletedProcess] = {}

        def _run_stalled_fire() -> None:
            result_holder["r"] = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)

        stalled_thread = threading.Thread(target=_run_stalled_fire)
        stalled_thread.start()

        # The scan (and its lock release) completes before jq -n is ever
        # invoked, so scan_state appearing proves the stall has begun --
        # the `sleep 5` stub itself gets capped to ~2s by the hook's own
        # _lib_capped_for wrapper around this jq -n call.
        deadline = time.monotonic() + 20.0
        while not scan_state.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert scan_state.exists(), "the scan must complete before the stubbed jq -n sleep starts"

        lock_dir = scan_state.parent / f"{scan_state.name}.lock"
        probe = subprocess.run(["mkdir", str(lock_dir)], stderr=subprocess.DEVNULL)
        probe_succeeded = probe.returncode == 0
        if probe_succeeded:
            lock_dir.rmdir()

        stalled_thread.join()
        assert result_holder["r"].returncode == 0

        assert probe_succeeded, (
            "the scan lock must already be released while the stalled fire is still building its nudge JSON, "
            "not held through the rest of the script"
        )

    # -------------------------------------------------------------------
    # Incremental counter correctness across multiple fires
    # -------------------------------------------------------------------

    def test_incremental_counter_accumulates_correctly_across_checked_fires(self, tmp_path):
        """The scan-state file's running total must equal the transcript's
        actual cumulative turn count at each checked fire, read from the
        hook's own persisted state rather than parsed out of the advisory
        prose message."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(50)])
        first_pass = _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        assert first_pass[-1].stdout.strip() == "", "50 turns is below threshold"
        offset1, total1, _ = _scan_state_path(tmp_path).read_text().splitlines()
        assert int(total1) == 50

        _append_to_transcript(transcript, [_assistant_turn_record() for _ in range(100)])
        second_pass = _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        assert second_pass[-1].stdout.strip() == "", "150 turns is still below the 340 threshold"
        offset2, total2, _ = _scan_state_path(tmp_path).read_text().splitlines()
        assert int(total2) == 150
        assert int(offset2) > int(offset1), "offset must advance as the transcript grows"

        _append_to_transcript(transcript, [_assistant_turn_record() for _ in range(200)])
        third_pass = _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        assert third_pass[-1].stdout.strip() != "", "350 cumulative turns crosses the 340 threshold"
        offset3, total3, _ = _scan_state_path(tmp_path).read_text().splitlines()
        assert int(total3) == 350
        assert int(offset3) == transcript.stat().st_size

    def test_bootstrap_scan_reads_the_whole_transcript_on_first_checked_fire(self, tmp_path):
        """No -scan file yet: the bootstrap path counts every turn already
        present (unlike the handoff-nudge hook's bounded `tail -n 200`
        bootstrap, a running total needs everything written so far)."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(DEFAULT_THRESHOLD + 10)])
        scan_state = _scan_state_path(tmp_path)
        assert not scan_state.exists()

        _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        offset, total, _ = scan_state.read_text().splitlines()
        assert int(total) == DEFAULT_THRESHOLD + 10
        assert int(offset) == transcript.stat().st_size

        lock_dir = scan_state.parent / f"{scan_state.name}.lock"
        assert not lock_dir.exists(), "a normal fire must release the scan lock on exit"

    def test_transcript_shrink_resets_offset_and_total_instead_of_compounding(self, tmp_path):
        """A transcript replaced by a smaller file (shrunk, or a stale path
        reused by a new dispatch) must reset the stored offset and total to
        0 rather than compounding the new count onto a stale total."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(200)])
        _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        offset1, total1, _ = _scan_state_path(tmp_path).read_text().splitlines()
        assert int(total1) == 200

        _write_transcript(transcript, [_assistant_turn_record() for _ in range(30)])
        assert transcript.stat().st_size < int(offset1), "the replacement must actually be smaller"

        _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        offset2, total2, _ = _scan_state_path(tmp_path).read_text().splitlines()
        assert int(total2) == 30, "total must reset to the smaller file's own turn count, not compound onto the stale total"
        assert int(offset2) == transcript.stat().st_size

    def test_shrink_after_misaligned_force_advance_resets_the_misaligned_flag_too(self, tmp_path):
        """A dispatch whose oversized-record force-advance already set
        misaligned=1 (see test_oversized_single_record_line_force_advances_instead_of_freezing
        below), then has its transcript shrink before the next fire, must
        reset misaligned to 0 alongside offset and total -- the same
        shrink-reset test_transcript_shrink_resets_offset_and_total_instead_of_compounding
        above checks for offset/total, extended to the third field."""
        transcript = tmp_path / "t.jsonl"
        oversized_record = {
            "type": "assistant",
            "message": {"role": "assistant", "usage": {"output_tokens": 1}, "content": "x" * 4_300},
        }
        _write_transcript(
            transcript,
            [oversized_record] + [_assistant_turn_record() for _ in range(20)],
        )
        extra_env = {"LONG_TURN_NUDGE_MAX_SCAN_WINDOW_BYTES": str(SMALL_SCAN_WINDOW_BYTES)}

        _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE, extra_env=extra_env)
        offset1, _, misaligned1 = _scan_state_path(tmp_path).read_text().splitlines()
        assert misaligned1 == "1", "the force-advance must mark SCAN_STATE_FILE's third line misaligned"

        _write_transcript(transcript, [_assistant_turn_record() for _ in range(5)])
        assert transcript.stat().st_size < int(offset1), "the replacement must actually be smaller"

        _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE, extra_env=extra_env)
        offset2, total2, misaligned2 = _scan_state_path(tmp_path).read_text().splitlines()
        assert int(total2) == 5, "total must reset to the smaller file's own turn count, not compound onto the stale total"
        assert int(offset2) == transcript.stat().st_size
        assert misaligned2 == "0", "the shrink-reset must clear a stale misaligned flag, not carry it forward onto the reset file"

    def test_legacy_two_line_scan_state_file_upgrades_gracefully(self, tmp_path):
        """A 2-line SCAN_STATE_FILE (offset, total -- no misaligned third
        line) must still be read without error and upgraded to the current
        3-line format on the next write, so a dispatch already running when
        another session's `git pull` updates the repo mid-flight doesn't
        crash on its own state file."""
        transcript = tmp_path / "t.jsonl"
        records = [_assistant_turn_record() for _ in range(50)]
        _write_transcript(transcript, records)
        first_record_line_bytes = len(json.dumps(records[0])) + 1  # +1 for the trailing newline

        marker_dir = _marker_dir(tmp_path)
        marker_dir.mkdir(parents=True)
        _scan_state_path(tmp_path).write_text(f"{first_record_line_bytes}\n1\n")  # legacy: offset, total, no misaligned line

        results = _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        assert all(r.returncode == 0 for r in results)
        lines = _scan_state_path(tmp_path).read_text().splitlines()
        assert len(lines) == 3, "the next write must upgrade the file to the current 3-line format"
        offset, total, misaligned = lines
        assert int(offset) == transcript.stat().st_size, "the scan must resume from the legacy offset, not restart at 0"
        assert int(total) == 50, "the legacy total must be carried forward and added to, not discarded"
        assert misaligned == "0"

    # -------------------------------------------------------------------
    # Sampled-fire-only stale marker sweep
    # -------------------------------------------------------------------

    def test_stale_marker_dir_entries_swept_only_on_sampled_fire(self, tmp_path):
        """The `find ... -mtime +30 -delete` sweep is the sole cleanup
        mechanism for MARKER_DIR and must run only on the cadence-sampled
        fire, not every fire -- a per-fire directory listing would undercut
        the cheap-append framing every other fire relies on. Also covers a
        stale `.lock` directory (e.g. leaked by a SIGKILL mid-scan -- see
        the hook's own header comment), since `find -delete` reclaims an
        empty stale directory the same way it reclaims a stale file."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(10)])
        marker_dir = _marker_dir(tmp_path)
        marker_dir.mkdir(parents=True)
        stale_file = marker_dir / "some-other-session"
        stale_file.write_text("stale\n")
        stale_time = time.time() - (31 * 86400)
        os.utime(stale_file, (stale_time, stale_time))
        stale_lock_dir = marker_dir / "some-other-session-scan.lock"
        stale_lock_dir.mkdir()
        os.utime(stale_lock_dir, (stale_time, stale_time))

        _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE - 1)
        assert stale_file.exists(), "sweep must not run before the sampled fire"
        assert stale_lock_dir.exists(), "sweep must not run before the sampled fire"

        _fire(transcript, tmp_path, 1)
        assert not stale_file.exists(), "sweep must run on the Nth (sampled) fire"
        assert not stale_lock_dir.exists(), "sweep must reclaim a leaked lock directory too"

    @pytest.mark.timing
    def test_stale_marker_sweep_timeout_does_not_hang_the_fire(self, tmp_path):
        """The `find ... -mtime +30 -delete` sweep is capped at 2s via
        _lib_capped_for -- a stalled find must not hang the sampled fire,
        and must degrade to no sweep rather than block. Primes the cadence
        with DEFAULT_SAMPLE_CADENCE - 1 untimed fires (find is never invoked
        before the sampled fire, so these are unaffected by the stub) and
        times only the single sampled fire that actually reaches `find`, the
        same pattern test_stale_marker_dir_entries_swept_only_on_sampled_fire
        above uses. Mirrors
        test_jq_count_stage_timeout_leaves_no_output_and_offset_unchanged
        below and test_diff_quiet_probe_times_out_to_no_match in
        test_marker_script.py."""
        if not shutil.which("timeout"):
            pytest.skip("timeout(1) not available — BSD/macOS without coreutils")
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(10)])

        marker_dir = _marker_dir(tmp_path)
        marker_dir.mkdir(parents=True)
        stale_file = marker_dir / "some-other-session"
        stale_file.write_text("stale\n")
        stale_time = time.time() - (31 * 86400)
        os.utime(stale_file, (stale_time, stale_time))

        real_find = shutil.which("find")
        stub_dir = tmp_path / "stub-bin"
        stub_dir.mkdir()
        stub = stub_dir / "find"
        stub.write_text(
            '#!/bin/bash\n'
            'for arg in "$@"; do\n'
            '  if [ "$arg" = "-mtime" ]; then\n'
            '    sleep 10\n'
            '    break\n'
            '  fi\n'
            'done\n'
            f'exec {real_find} "$@"\n'
        )
        stub.chmod(0o755)
        extra_env = {"PATH": f"{stub_dir}:{os.environ['PATH']}"}

        _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE - 1, extra_env=extra_env)

        start = time.monotonic()
        result = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)
        elapsed = time.monotonic() - start

        assert result.returncode == 0
        # No upper bound: an empirically observed baseline, not a guessed
        # margin -- the invariant that matters is not hanging for the ~10s
        # stub sleep, which the lower bound alone already rules out.
        assert elapsed >= 1.5, (
            f"expected the 2s _lib_capped_for timeout to fire (stub sleeps 10s "
            f"if it does not), took {elapsed:.1f}s for the single sampled fire"
        )
        assert stale_file.exists(), (
            "a timed-out sweep must not have silently run to completion and reclaimed the stale entry"
        )

    # -------------------------------------------------------------------
    # Mid-write transcript safety
    # -------------------------------------------------------------------

    def test_incomplete_trailing_line_is_not_counted_or_offset_past(self, tmp_path):
        """A fire mid-write, where the transcript's newly appended bytes are
        an incomplete JSON line: the stored offset must stop before it and
        the partial record must not be counted -- mirrors
        nudge-handoff-near-context-cap.sh's own mid-write test, since both
        hooks share _lib_advance_offset_past_complete_lines."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(50)])
        _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        offset_after_first, total_after_first, _ = _scan_state_path(tmp_path).read_text().splitlines()
        assert int(total_after_first) == 50
        offset_after_first_size = transcript.stat().st_size

        full_line = json.dumps(_assistant_turn_record())
        split_at = len(full_line) // 2
        with transcript.open("a") as f:
            f.write(full_line[:split_at])  # no trailing newline: caught mid-write

        results = _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        assert all(r.returncode == 0 for r in results)
        offset, total, _ = _scan_state_path(tmp_path).read_text().splitlines()
        assert int(offset) == offset_after_first_size, "offset must not advance past the incomplete trailing line"
        assert int(total) == 50, "the partial record must not be counted yet"

        with transcript.open("a") as f:
            f.write(full_line[split_at:] + "\n")  # complete the line

        _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        offset2, total2, _ = _scan_state_path(tmp_path).read_text().splitlines()
        assert int(total2) == 51, "the completed record must be counted once whole, not dropped or double-counted"
        assert int(offset2) == transcript.stat().st_size

    def test_jq_count_failure_leaves_scan_state_offset_unchanged(self, tmp_path):
        """`jq -s` failing (or emitting non-digit output) on the appended
        slice must not advance the stored offset -- otherwise the
        un-rescanned bytes would be silently skipped and never counted. The
        stub only intercepts the `-s` (count-stage) invocation, leaving the
        hook's other jq calls (input parsing, output-JSON build) untouched."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(50)])
        _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        offset_before, total_before, _ = _scan_state_path(tmp_path).read_text().splitlines()
        assert int(total_before) == 50

        _append_to_transcript(transcript, [_assistant_turn_record() for _ in range(20)])

        real_jq = shutil.which("jq")
        stub_dir = tmp_path / "stub-bin"
        stub_dir.mkdir()
        stub = stub_dir / "jq"
        stub.write_text(
            '#!/bin/bash\n'
            'if [ "$1" = "-s" ]; then\n'
            '  echo "not-a-number"\n'
            '  exit 1\n'
            'fi\n'
            f'exec {real_jq} "$@"\n'
        )
        stub.chmod(0o755)

        results = _fire(
            transcript, tmp_path, DEFAULT_SAMPLE_CADENCE,
            extra_env={"PATH": f"{stub_dir}:{os.environ['PATH']}"},
        )
        assert all(r.returncode == 0 for r in results)
        offset_after, total_after, _ = _scan_state_path(tmp_path).read_text().splitlines()
        assert offset_after == offset_before, "offset must not advance past bytes jq failed to count"
        assert int(total_after) == 50, "uncounted bytes must not be folded into the running total"

        # jq succeeds on the next fire: the same bytes must be counted
        # exactly once now, not skipped forever and not double-counted.
        _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        offset_final, total_final, _ = _scan_state_path(tmp_path).read_text().splitlines()
        assert int(total_final) == 70
        assert int(offset_final) == transcript.stat().st_size

    @pytest.mark.timing
    def test_jq_count_stage_timeout_leaves_no_output_and_offset_unchanged(self, tmp_path):
        """The `jq -s` count call is capped at 2s via _lib_capped_for -- a
        stalled jq must not hang the fire, and must degrade to no scan
        progress (offset/total unchanged, no nudge output) rather than
        block. Primes the cadence with DEFAULT_SAMPLE_CADENCE - 1 untimed
        fires (the count stage is never invoked before the sampled fire, so
        these are unaffected by the stub) and times only the single sampled
        fire that actually reaches `jq -s`."""
        if not shutil.which("timeout"):
            pytest.skip("timeout(1) not available — BSD/macOS without coreutils")
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(50)])
        _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        offset_before, total_before, _ = _scan_state_path(tmp_path).read_text().splitlines()

        _append_to_transcript(transcript, [_assistant_turn_record() for _ in range(20)])

        real_jq = shutil.which("jq")
        stub_dir = tmp_path / "stub-bin"
        stub_dir.mkdir()
        stub = stub_dir / "jq"
        stub.write_text(
            '#!/bin/bash\n'
            'if [ "$1" = "-s" ]; then\n'
            '  sleep 10\n'
            'fi\n'
            f'exec {real_jq} "$@"\n'
        )
        stub.chmod(0o755)
        extra_env = {"PATH": f"{stub_dir}:{os.environ['PATH']}"}

        pre_fires = _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE - 1, extra_env=extra_env)
        assert all(r.stdout.strip() == "" for r in pre_fires), "unsampled fires must stay silent regardless of the jq stub"

        start = time.monotonic()
        result = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)
        elapsed = time.monotonic() - start

        assert result.returncode == 0
        assert result.stdout.strip() == "", "a timed-out scan must not fire a nudge"
        # No upper bound: an empirically observed baseline, not a guessed
        # margin -- the invariant that matters is not hanging for the ~10s
        # stub sleep, which the lower bound alone already rules out.
        assert elapsed >= 1.5, (
            f"expected the 2s _lib_capped_for timeout to fire (stub sleeps 10s "
            f"if it does not), took {elapsed:.1f}s for the single sampled fire"
        )
        offset_after, total_after, _ = _scan_state_path(tmp_path).read_text().splitlines()
        assert offset_after == offset_before, "a timed-out scan must not advance the offset"
        assert total_after == total_before, "a timed-out scan must not add to the running total"

    # -------------------------------------------------------------------
    # Bounded scan window (a retry must never grow past a fixed cap)
    # -------------------------------------------------------------------

    @pytest.mark.parametrize("malformed_value", MALFORMED_GUARD_VALUES)
    def test_malformed_max_scan_window_bytes_override_falls_back_to_default(self, tmp_path, malformed_value):
        """A malformed override (notably "0") must fall back to the shipped
        2,000,000-byte default, not degrade toward scan_window_end ==
        scan_from, which would permanently freeze the offset at 0."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(50)])
        results = _fire(
            transcript, tmp_path, DEFAULT_SAMPLE_CADENCE,
            extra_env={"LONG_TURN_NUDGE_MAX_SCAN_WINDOW_BYTES": malformed_value},
        )
        assert all(r.returncode == 0 for r in results)
        offset, total, _ = _scan_state_path(tmp_path).read_text().splitlines()
        assert int(offset) == transcript.stat().st_size, (
            f"malformed override {malformed_value!r} must fall back to the shipped default, not freeze the offset at 0"
        )
        assert int(total) == 50

    def test_oversized_backlog_scan_advances_by_bounded_window_not_all_at_once(self, tmp_path):
        """A backlog larger than the per-attempt scan window must be
        consumed across multiple sampled fires, never in one unbounded
        tail|jq -s read. Overrides LONG_TURN_NUDGE_MAX_SCAN_WINDOW_BYTES to
        a KB-scale window so this pins the same branch logic without a
        multi-MB fixture."""
        transcript = tmp_path / "t.jsonl"
        oversized_record = {
            "type": "assistant",
            "message": {"role": "assistant", "usage": {"output_tokens": 1}, "content": "x" * 900},
        }
        _write_transcript(transcript, [oversized_record for _ in range(10)])  # comfortably exceeds the overridden window cap
        extra_env = {"LONG_TURN_NUDGE_MAX_SCAN_WINDOW_BYTES": str(SMALL_SCAN_WINDOW_BYTES)}

        _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE, extra_env=extra_env)
        offset, total, _ = _scan_state_path(tmp_path).read_text().splitlines()
        full_size = transcript.stat().st_size
        assert int(offset) < full_size, "a single bounded scan attempt must not consume an oversized backlog in one shot"

        max_sampled_fires = 20
        fires = 0
        while int(offset) < full_size and fires < max_sampled_fires:
            _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE, extra_env=extra_env)
            offset, total, _ = _scan_state_path(tmp_path).read_text().splitlines()
            fires += 1

        assert int(offset) == full_size, "bounded windowing must still fully catch up given enough sampled fires"
        assert int(total) == 10

    def test_bounded_window_avoids_jq_timeout_that_an_unbounded_read_would_hit(self, tmp_path):
        """A jq stub times out on input over MAX_SCAN_WINDOW_BYTES + 1 and
        succeeds below it, sized so a single unbounded tail|jq -s read of
        the whole backlog would always hit the stub's timeout path, but
        each MAX_SCAN_WINDOW_BYTES-bounded attempt stays under the
        threshold and succeeds. Proves the window bound is what lets the
        scan make progress on a timeout-prone backlog -- the test above
        proves only that the window itself stays bounded, using a real jq
        that succeeds regardless of size. The flat repeated-character
        fixture pins the byte-bound mechanism only, not the per-byte parse
        cost of real, structurally complex transcript content."""
        if not shutil.which("timeout"):
            pytest.skip("timeout(1) not available — BSD/macOS without coreutils")
        transcript = tmp_path / "t.jsonl"
        oversized_record = {
            "type": "assistant",
            "message": {"role": "assistant", "usage": {"output_tokens": 1}, "content": "x" * 900},
        }
        _write_transcript(transcript, [oversized_record for _ in range(10)])  # ~10 KB backlog, well over the stub's threshold

        real_jq = shutil.which("jq")
        stub_dir = tmp_path / "stub-bin"
        stub_dir.mkdir()
        stub = stub_dir / "jq"
        stub.write_text(
            '#!/bin/bash\n'
            'if [ "$1" = "-s" ]; then\n'
            '  tmp=$(mktemp)\n'
            '  cat > "$tmp"\n'
            '  size=$(wc -c < "$tmp")\n'
            # Threshold = the LONG_TURN_NUDGE_MAX_SCAN_WINDOW_BYTES override
            # set below (SMALL_SCAN_WINDOW_BYTES) + 1.
            f'  if [ "$size" -gt {SMALL_SCAN_WINDOW_BYTES + 1} ]; then\n'
            '    rm -f "$tmp"\n'
            '    sleep 10\n'
            '    exit 1\n'
            '  fi\n'
            f'  {real_jq} -s \'[.[] | select(.message? and .message.usage)] | length\' < "$tmp"\n'
            '  rm -f "$tmp"\n'
            '  exit 0\n'
            'fi\n'
            f'exec {real_jq} "$@"\n'
        )
        stub.chmod(0o755)
        extra_env = {
            "PATH": f"{stub_dir}:{os.environ['PATH']}",
            "LONG_TURN_NUDGE_MAX_SCAN_WINDOW_BYTES": str(SMALL_SCAN_WINDOW_BYTES),
        }

        _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE, extra_env=extra_env)
        offset, total, _ = _scan_state_path(tmp_path).read_text().splitlines()
        full_size = transcript.stat().st_size

        max_sampled_fires = 20
        fires = 0
        while int(offset) < full_size and fires < max_sampled_fires:
            _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE, extra_env=extra_env)
            offset, total, _ = _scan_state_path(tmp_path).read_text().splitlines()
            fires += 1

        assert int(offset) == full_size, "each bounded window's jq input must stay under the stub's timeout threshold"
        assert int(total) == 10

    def test_oversized_single_record_line_force_advances_instead_of_freezing(self, tmp_path):
        """A single JSONL record whose serialized line exceeds
        MAX_SCAN_WINDOW_BYTES has no newline anywhere in a full window read.
        The offset must force-advance past it and eventually resync at its
        terminating newline, rather than re-reading the identical byte
        range forever. Overrides LONG_TURN_NUDGE_MAX_SCAN_WINDOW_BYTES to a
        KB-scale window so this pins the same branch logic without a
        multi-MB fixture."""
        transcript = tmp_path / "t.jsonl"
        oversized_record = {
            "type": "assistant",
            "message": {"role": "assistant", "usage": {"output_tokens": 1}, "content": "x" * 4_300},
        }
        _write_transcript(
            transcript,
            [oversized_record] + [_assistant_turn_record() for _ in range(20)],
        )
        extra_env = {"LONG_TURN_NUDGE_MAX_SCAN_WINDOW_BYTES": str(SMALL_SCAN_WINDOW_BYTES)}

        _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE, extra_env=extra_env)
        offset, total, misaligned = _scan_state_path(tmp_path).read_text().splitlines()
        full_size = transcript.stat().st_size
        assert int(offset) > 0, "the offset must force-advance past the oversized record, not freeze at 0"
        assert int(offset) < full_size, "the first sampled fire must not consume the whole backlog in one attempt"
        assert misaligned == "1", "the force-advance must mark SCAN_STATE_FILE's third line misaligned"

        max_sampled_fires = 20
        fires = 0
        while int(offset) < full_size and fires < max_sampled_fires:
            _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE, extra_env=extra_env)
            offset, total, _ = _scan_state_path(tmp_path).read_text().splitlines()
            fires += 1

        assert int(offset) == full_size, "the scan must eventually reach the end of the transcript, not stall permanently"
        assert int(total) == 20, "the oversized record's own turn is undercounted, but every later record's turn is not"

    def test_consecutive_oversized_record_lines_still_converge_via_repeated_force_advance(self, tmp_path):
        """Two oversized records back to back: the window immediately after
        the first record's force-advance resync is itself a full,
        no-newline window (still inside the second record's own oversized
        line), which must force-advance again rather than treating that
        window as the second record's true resync point."""
        transcript = tmp_path / "t.jsonl"
        oversized_record = {
            "type": "assistant",
            "message": {"role": "assistant", "usage": {"output_tokens": 1}, "content": "x" * 4_300},
        }
        _write_transcript(
            transcript,
            [oversized_record, oversized_record] + [_assistant_turn_record() for _ in range(7)],
        )
        extra_env = {"LONG_TURN_NUDGE_MAX_SCAN_WINDOW_BYTES": str(SMALL_SCAN_WINDOW_BYTES)}
        full_size = transcript.stat().st_size

        offset, total = "0", "0"
        max_sampled_fires = 20
        fires = 0
        while int(offset) < full_size and fires < max_sampled_fires:
            _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE, extra_env=extra_env)
            offset, total, _ = _scan_state_path(tmp_path).read_text().splitlines()
            fires += 1

        assert int(offset) == full_size, "consecutive oversized records must not permanently freeze the offset"
        assert int(total) == 7, "both oversized records' own turns are undercounted, but every later record's is not"

    # -------------------------------------------------------------------
    # JSON contract shape (must match nudge-handoff-near-context-cap.sh)
    # -------------------------------------------------------------------

    def test_json_contract_shape_matches_handoff_nudge_hook(self, tmp_path):
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(DEFAULT_THRESHOLD + 1)])
        results = _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        output = json.loads(results[-1].stdout)
        assert set(output.keys()) == {"hookSpecificOutput"}
        assert set(output["hookSpecificOutput"].keys()) == {"hookEventName", "additionalContext"}
        assert output["hookSpecificOutput"]["hookEventName"] == "PostToolBatch"
        assert isinstance(output["hookSpecificOutput"]["additionalContext"], str)
        assert output["hookSpecificOutput"]["additionalContext"] != ""

    def test_unrecognized_hook_event_name_falls_back_to_post_tool_batch(self, tmp_path):
        """An unrecognized hook_event_name (this hook is only registered
        under PostToolBatch) must still emit hookEventName=PostToolBatch,
        mirroring nudge-handoff-near-context-cap.sh's own fallback
        treatment of this same untrusted jq-extracted field."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(DEFAULT_THRESHOLD + 1)])
        payload = _base_payload(transcript, hook_event_name="Stop")
        results = [_run_hook(payload, tmp_path) for _ in range(DEFAULT_SAMPLE_CADENCE)]
        output = json.loads(results[-1].stdout)
        assert output["hookSpecificOutput"]["hookEventName"] == "PostToolBatch"

    def test_fire_writes_a_log_line(self, tmp_path):
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(DEFAULT_THRESHOLD + 1)])
        _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        log_text = _log_path(tmp_path).read_text()
        assert f"session={SESSION_ID}" in log_text
        assert "event=PostToolBatch" in log_text

    # -------------------------------------------------------------------
    # Never blocks or kills the observed tool call
    # -------------------------------------------------------------------

    def test_never_exits_nonzero_even_far_past_threshold_across_many_fires(self, tmp_path):
        """Unlike nudge-handoff-near-context-cap.sh's escalation ladder (a
        further re-arm past HANDOFF_NUDGE_BLOCK_AFTER hard-blocks with exit
        2), this hook is purely advisory on every fire -- no exit-2 path
        exists at all, no matter how many cadence windows pass."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(3_000)])
        results = _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE * 5)
        assert all(r.returncode == 0 for r in results)
        assert all(r.stderr.strip() == "" for r in results)

    def test_missing_transcript_fails_open(self, tmp_path):
        missing = tmp_path / "does-not-exist.jsonl"
        payload = _base_payload(missing)
        result = _run_hook(payload, tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_jq_absent_fails_open_not_hard_blocked(self, tmp_path):
        """jq backs input parsing, the turn-count scan, and the output-JSON
        build -- its absence fails open at the first of these, input
        parsing, before any fire reaches the scan or output-build jq calls.
        Never hard-blocks, unlike nudge-handoff-near-context-cap.sh's
        escalation ladder. Mirrors test_jq_absent_fails_open_not_hard_blocked
        in test_nudge_handoff_near_context_cap.py."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(DEFAULT_THRESHOLD + 5)])
        farm_dir = tmp_path / "path-without-jq"
        farm_dir.mkdir()
        restricted_path = build_path_without("jq", farm_dir)
        results = _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE, extra_env={"PATH": restricted_path})
        assert all(r.returncode == 0 for r in results)
        assert all(r.stdout.strip() == "" for r in results)
        assert all(r.stderr.strip() == "" for r in results)

    def test_wc_absent_fails_open_not_hard_blocked(self, tmp_path):
        """`wc` backs the invocation-count cadence check and the
        incremental-scan offset math -- its absence fails open at the
        first of these, the cadence check, before any fire reaches the
        scan's own wc calls. Fails open like every other missing-dependency
        case in this file, never hard-block."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(DEFAULT_THRESHOLD + 5)])
        farm_dir = tmp_path / "path-without-wc"
        farm_dir.mkdir()
        restricted_path = build_path_without("wc", farm_dir)
        results = _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE, extra_env={"PATH": restricted_path})
        assert all(r.returncode == 0 for r in results)
        assert all(r.stdout.strip() == "" for r in results)
        assert all(r.stderr.strip() == "" for r in results)

    def test_tail_absent_fails_open_not_hard_blocked(self, tmp_path):
        """`tail` backs both the windowed scan read and
        _lib_advance_offset_past_complete_lines; its absence must fail open
        like every other missing-dependency case in this file, never
        hard-block."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(DEFAULT_THRESHOLD + 5)])
        farm_dir = tmp_path / "path-without-tail"
        farm_dir.mkdir()
        restricted_path = build_path_without("tail", farm_dir)
        results = _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE, extra_env={"PATH": restricted_path})
        assert all(r.returncode == 0 for r in results)
        assert all(r.stdout.strip() == "" for r in results)
        assert all(r.stderr.strip() == "" for r in results)

    def test_tail_absent_after_a_real_scan_leaves_scan_state_unchanged(self, tmp_path):
        """Preserving state on a missing `tail` is only meaningful once
        there's non-zero state to preserve -- a cold-start run can't tell
        'correctly preserved' from 'always zero anyway'. Establishes a real
        50-turn scan first, then strips `tail` and re-fires, mirroring
        test_jq_count_failure_leaves_scan_state_offset_unchanged's two-phase
        shape."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(50)])
        _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        offset_before, total_before, _ = _scan_state_path(tmp_path).read_text().splitlines()
        assert int(total_before) == 50

        _append_to_transcript(transcript, [_assistant_turn_record() for _ in range(20)])
        farm_dir = tmp_path / "path-without-tail"
        farm_dir.mkdir()
        restricted_path = build_path_without("tail", farm_dir)
        results = _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE, extra_env={"PATH": restricted_path})
        assert all(r.returncode == 0 for r in results)
        assert all(r.stdout.strip() == "" for r in results)

        offset_after, total_after, _ = _scan_state_path(tmp_path).read_text().splitlines()
        assert offset_after == offset_before, "a scan that fails open on a missing tail must not regress the stored offset"
        assert total_after == total_before, "a scan that fails open on a missing tail must not regress the running total"

    def test_mktemp_absent_fails_open_not_hard_blocked(self, tmp_path):
        """`mktemp` backs materializing the bounded scan window to a temp
        file; its absence must fail open like every other missing-dependency
        case in this file, never hard-block."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(DEFAULT_THRESHOLD + 5)])
        farm_dir = tmp_path / "path-without-mktemp"
        farm_dir.mkdir()
        restricted_path = build_path_without("mktemp", farm_dir)
        results = _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE, extra_env={"PATH": restricted_path})
        assert all(r.returncode == 0 for r in results)
        assert all(r.stdout.strip() == "" for r in results)
        assert all(r.stderr.strip() == "" for r in results)

    def test_mktemp_absent_after_a_real_scan_leaves_scan_state_unchanged(self, tmp_path):
        """Same two-phase shape as
        test_tail_absent_after_a_real_scan_leaves_scan_state_unchanged, for
        the documented 'missing mktemp is treated as a jq failure' fallback:
        establishes a real 50-turn scan first, then strips `mktemp` and
        re-fires, so a cold-start run can't mask a regression that zeroes or
        otherwise mutates the preserved state."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(50)])
        _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        offset_before, total_before, _ = _scan_state_path(tmp_path).read_text().splitlines()
        assert int(total_before) == 50

        _append_to_transcript(transcript, [_assistant_turn_record() for _ in range(20)])
        farm_dir = tmp_path / "path-without-mktemp"
        farm_dir.mkdir()
        restricted_path = build_path_without("mktemp", farm_dir)
        results = _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE, extra_env={"PATH": restricted_path})
        assert all(r.returncode == 0 for r in results)
        assert all(r.stdout.strip() == "" for r in results)

        offset_after, total_after, _ = _scan_state_path(tmp_path).read_text().splitlines()
        assert offset_after == offset_before, "a scan that fails open on a missing mktemp must not regress the stored offset"
        assert total_after == total_before, "a scan that fails open on a missing mktemp must not regress the running total"

    def test_traversal_session_id_writes_nothing_outside_marker_dir(self, tmp_path):
        """A session_id containing '../' must not let any of this hook's
        writes (FIRED_MARKER, the -invocations counter, the -scan state
        file) escape .long-turn-nudge-fired.d/ -- this hook trusts
        session_id straight from the PostToolBatch payload rather than
        resolving it from a session file the way marker.sh does, so the
        `_lib_valid_session_id_component` guard is the sole defense.
        Mirrors TestMarkerScriptSessionIdValidation's
        test_no_marker_written_for_path_escaping_session_id in
        test_marker_script.py."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(DEFAULT_THRESHOLD + 5)])
        (tmp_path / ".claude").mkdir()
        canary = plant_traversal_canary(tmp_path)

        results = _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE, session_id=TRAVERSAL_SESSION_ID)

        assert all(r.returncode == 0 for r in results)
        assert all(r.stdout.strip() == "" for r in results)
        assert canary.read_text() == CANARY_CONTENT, (
            "a traversal session_id must not touch a file outside .long-turn-nudge-fired.d/"
        )

    def test_scan_treats_adversarial_transcript_content_as_inert_data(self, tmp_path):
        """A transcript record's .message.content can hold arbitrary
        attacker-controlled text -- shell metacharacters and an embedded NUL
        (written here as the literal escape sequence \\u0000, since that's
        how json.dumps encodes a NUL on disk -- no raw NUL byte transits
        the pipeline) must never reach a shell. _scan_turn_count_cached
        pipes transcript bytes through tail/head/jq, never eval or exec, so
        this content is inert data at every stage of the pipeline. Mirrors
        test_traversal_session_id_writes_nothing_outside_marker_dir's
        canary-file assertion style."""
        canary = tmp_path / "pwned"
        transcript = tmp_path / "t.jsonl"
        records = [_assistant_turn_record() for _ in range(DEFAULT_THRESHOLD + 4)]
        records.append({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "usage": {"output_tokens": 1},
                "content": f"$(touch {canary}) `touch {canary}` \x00",
            },
        })
        _write_transcript(transcript, records)

        results = _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)

        assert all(r.returncode == 0 for r in results)
        assert not canary.exists(), "adversarial transcript content must not reach a shell"
        _, total, _ = _scan_state_path(tmp_path).read_text().splitlines()
        assert int(total) == DEFAULT_THRESHOLD + 5

    def test_missing_session_id_fails_open(self, tmp_path):
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(DEFAULT_THRESHOLD + 1)])
        payload = _base_payload(transcript)
        payload["session_id"] = ""
        result = _run_hook(payload, tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_malformed_stdin_fails_open(self, tmp_path):
        env = {**os.environ, "HOME": str(tmp_path)}
        env.pop("CLAUDE_CONFIG_DIR", None)
        result = subprocess.run(
            [str(NUDGE_HOOK)], input="not json", capture_output=True, text=True, env=env, check=False,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""
