"""Tests for nudge-long-turn-subagent.sh.

The hook is a PostToolBatch hook that emits a hookSpecificOutput.additionalContext
JSON payload when a *subagent dispatch's own* turn count crosses a measured
outlier threshold (340, just above the corpus-measured p99 of 339 turns --
see docs/design-decisions.md). Unlike nudge-handoff-near-context-cap.sh,
which fires only in the main session, this hook fires only when agent_type
identifies a subagent. Turn count is tracked incrementally per dispatch via a
small state file (resume offset + running total), reusing
_lib_advance_offset_past_complete_lines (shared with
nudge-handoff-near-context-cap.sh via _lib.sh) for mid-write transcript
safety. The scan/threshold check runs only on every SAMPLE_CADENCE'th fire;
every other fire only appends one byte to a counter file. The hook never
blocks or kills the tool call it observes, and fires at most once per
dispatch (no re-arm, no escalation ladder).

All tests sandbox $HOME via monkeypatch so markers and logs land in tmp_path
rather than the real $HOME.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from helpers import HOOKS_DIR

NUDGE_HOOK = HOOKS_DIR / "nudge-long-turn-subagent.sh"

SESSION_ID = "test-session-long-turn-001"

# Mirrors the hook's own shipped defaults so no test hand-computes them.
DEFAULT_THRESHOLD = 340
DEFAULT_SAMPLE_CADENCE = 10

# The five malformed shapes resolve_threshold/resolve_sample_cadence guard
# against: empty, a literal zero, non-digit, zero-padded, and 9+ digits
# (which risks wrapping negative in bash's signed 64-bit arithmetic).
MALFORMED_GUARD_VALUES = ["", "0", "not-a-number", "0340", "999999999"]


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
        offset1, total1 = _scan_state_path(tmp_path).read_text().splitlines()
        assert int(total1) == 50

        _append_to_transcript(transcript, [_assistant_turn_record() for _ in range(100)])
        second_pass = _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        assert second_pass[-1].stdout.strip() == "", "150 turns is still below the 340 threshold"
        offset2, total2 = _scan_state_path(tmp_path).read_text().splitlines()
        assert int(total2) == 150
        assert int(offset2) > int(offset1), "offset must advance as the transcript grows"

        _append_to_transcript(transcript, [_assistant_turn_record() for _ in range(200)])
        third_pass = _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        assert third_pass[-1].stdout.strip() != "", "350 cumulative turns crosses the 340 threshold"
        offset3, total3 = _scan_state_path(tmp_path).read_text().splitlines()
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
        offset, total = scan_state.read_text().splitlines()
        assert int(total) == DEFAULT_THRESHOLD + 10
        assert int(offset) == transcript.stat().st_size

    def test_transcript_shrink_resets_offset_and_total_instead_of_compounding(self, tmp_path):
        """A transcript replaced by a smaller file (shrunk, or a stale path
        reused by a new dispatch) must reset the stored offset and total to
        0 rather than compounding the new count onto a stale total."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(200)])
        _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        offset1, total1 = _scan_state_path(tmp_path).read_text().splitlines()
        assert int(total1) == 200

        _write_transcript(transcript, [_assistant_turn_record() for _ in range(30)])
        assert transcript.stat().st_size < int(offset1), "the replacement must actually be smaller"

        _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        offset2, total2 = _scan_state_path(tmp_path).read_text().splitlines()
        assert int(total2) == 30, "total must reset to the smaller file's own turn count, not compound onto the stale total"
        assert int(offset2) == transcript.stat().st_size

    # -------------------------------------------------------------------
    # Sampled-fire-only stale marker sweep
    # -------------------------------------------------------------------

    def test_stale_marker_dir_entries_swept_only_on_sampled_fire(self, tmp_path):
        """The `find ... -mtime +30 -delete` sweep is the sole cleanup
        mechanism for MARKER_DIR and must run only on the cadence-sampled
        fire, not every fire -- a per-fire directory listing would undercut
        the cheap-append framing every other fire relies on."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(10)])
        marker_dir = _marker_dir(tmp_path)
        marker_dir.mkdir(parents=True)
        stale_file = marker_dir / "some-other-session"
        stale_file.write_text("stale\n")
        stale_time = time.time() - (31 * 86400)
        os.utime(stale_file, (stale_time, stale_time))

        _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE - 1)
        assert stale_file.exists(), "sweep must not run before the sampled fire"

        _fire(transcript, tmp_path, 1)
        assert not stale_file.exists(), "sweep must run on the Nth (sampled) fire"

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
        offset_after_first, total_after_first = _scan_state_path(tmp_path).read_text().splitlines()
        assert int(total_after_first) == 50
        offset_after_first_size = transcript.stat().st_size

        full_line = json.dumps(_assistant_turn_record())
        split_at = len(full_line) // 2
        with transcript.open("a") as f:
            f.write(full_line[:split_at])  # no trailing newline: caught mid-write

        results = _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        assert all(r.returncode == 0 for r in results)
        offset, total = _scan_state_path(tmp_path).read_text().splitlines()
        assert int(offset) == offset_after_first_size, "offset must not advance past the incomplete trailing line"
        assert int(total) == 50, "the partial record must not be counted yet"

        with transcript.open("a") as f:
            f.write(full_line[split_at:] + "\n")  # complete the line

        _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        offset2, total2 = _scan_state_path(tmp_path).read_text().splitlines()
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
        offset_before, total_before = _scan_state_path(tmp_path).read_text().splitlines()
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
        offset_after, total_after = _scan_state_path(tmp_path).read_text().splitlines()
        assert offset_after == offset_before, "offset must not advance past bytes jq failed to count"
        assert int(total_after) == 50, "uncounted bytes must not be folded into the running total"

        # jq succeeds on the next fire: the same bytes must be counted
        # exactly once now, not skipped forever and not double-counted.
        _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        offset_final, total_final = _scan_state_path(tmp_path).read_text().splitlines()
        assert int(total_final) == 70
        assert int(offset_final) == transcript.stat().st_size

    @pytest.mark.timing
    def test_jq_count_stage_timeout_leaves_no_output_and_offset_unchanged(self, tmp_path):
        """The `jq -s` count call is capped at 2s via _lib_capped_for -- a
        stalled jq must not hang the fire, and must degrade to no scan
        progress (offset/total unchanged, no nudge output) rather than
        block."""
        if not shutil.which("timeout"):
            pytest.skip("timeout(1) not available — BSD/macOS without coreutils")
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_turn_record() for _ in range(50)])
        _fire(transcript, tmp_path, DEFAULT_SAMPLE_CADENCE)
        offset_before, total_before = _scan_state_path(tmp_path).read_text().splitlines()

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

        start = time.monotonic()
        results = _fire(
            transcript, tmp_path, DEFAULT_SAMPLE_CADENCE,
            extra_env={"PATH": f"{stub_dir}:{os.environ['PATH']}"},
        )
        elapsed = time.monotonic() - start

        assert all(r.returncode == 0 for r in results)
        assert all(r.stdout.strip() == "" for r in results), "a timed-out scan must not fire a nudge"
        assert elapsed < 8.0, (
            f"expected the 2s _lib_capped_for timeout to fire (stub sleeps 10s "
            f"if it does not), took {elapsed:.1f}s across {DEFAULT_SAMPLE_CADENCE} fires"
        )
        offset_after, total_after = _scan_state_path(tmp_path).read_text().splitlines()
        assert offset_after == offset_before, "a timed-out scan must not advance the offset"
        assert total_after == total_before, "a timed-out scan must not add to the running total"

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
