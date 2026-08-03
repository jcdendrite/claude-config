"""Tests for nudge-handoff-near-context-cap.sh.

The hook is a UserPromptSubmit hook that emits a one-shot
hookSpecificOutput.additionalContext JSON payload when the estimated carried
token count crosses 60% of the resolved model's context window (200k models:
120 000; 1M models: 600 000; unrecognized/missing model IDs default to the 1M
window). The nudge fires once per session — a marker file gates subsequent
turns.

All tests sandbox $HOME via monkeypatch so markers and logs land in tmp_path
rather than the real $HOME.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from helpers import HOOKS_DIR, TRAVERSAL_SESSION_ID

NUDGE_HOOK = HOOKS_DIR / "nudge-handoff-near-context-cap.sh"

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

SESSION_ID = "test-session-nudge-001"

# Mirrors the hook's own window table so no test hand-computes a threshold.
LARGE_WINDOW = 1_000_000
SMALL_WINDOW = 200_000
LARGE_THRESHOLD = 600_000
SMALL_THRESHOLD = 120_000
ABOVE_LARGE = 650_000

# Four verified 200k models and four verified 1M models, chosen so the two
# 1M Opus rows flank the 200k Opus 4.5/4.1 entries — a same-major-version
# sanity check that 4.5/4.1 (200k) and 4.6/4.8 (1M) don't collapse onto the
# same window under a careless broad `case` arm (e.g. claude-opus-4-*).
KNOWN_MODEL_WINDOWS = [
    ("claude-haiku-4-5", SMALL_WINDOW),
    ("claude-sonnet-4-5", SMALL_WINDOW),
    ("claude-opus-4-5", SMALL_WINDOW),
    ("claude-opus-4-1", SMALL_WINDOW),
    ("claude-sonnet-5", LARGE_WINDOW),
    ("claude-opus-5", LARGE_WINDOW),
    ("claude-opus-4-6", LARGE_WINDOW),
    ("claude-opus-4-8", LARGE_WINDOW),
]

# IDs that share a known 200k arm's literal string prefix but extend it with
# more digits (claude-opus-4-1 vs claude-opus-4-10) — must NOT match that
# arm's glob and must resolve to the 1M default instead. Distinct from
# KNOWN_MODEL_WINDOWS's broad-glob guard above: this pins the opposite
# failure, a narrow glob's own prefix colliding with a longer numeral.
COLLIDING_MODEL_IDS = [
    "claude-opus-4-10",
    "claude-opus-4-15-20261101",
    "claude-haiku-4-50",
    "claude-sonnet-4-50",
]


def _assistant_record(
    *,
    cache_read: int = 0,
    cache_create: int = 0,
    input_tok: int = 0,
    output_tok: int = 0,
    model: str | None = "claude-sonnet-4-6",
) -> dict:
    """Build a minimal transcript assistant record with the given usage fields.

    model=None omits the "model" key entirely (missing-field case).
    """
    message = {
        "content": [],
        "usage": {
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_create,
            "input_tokens": input_tok,
            "output_tokens": output_tok,
        },
    }
    if model is not None:
        message["model"] = model
    return {"type": "assistant", "message": message}


def _record_totalling(total: int, *, model: str | None = "claude-sonnet-4-6") -> dict:
    """Build an assistant record whose four usage fields sum to `total`."""
    return _assistant_record(cache_read=total, model=model)


def _write_transcript(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def _run_hook(payload: dict, tmp_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(NUDGE_HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(tmp_path)},
        check=False,
    )


def _base_payload(transcript_path: Path, session_id: str = SESSION_ID) -> dict:
    return {
        "session_id": session_id,
        "transcript_path": str(transcript_path),
    }


def _log_path(tmp_path: Path) -> Path:
    return tmp_path / ".claude" / ".handoff-nudge.log"


def _marker_path(tmp_path: Path, session_id: str = SESSION_ID) -> Path:
    return tmp_path / ".claude" / ".handoff-nudge-fired.d" / session_id


def _drift_marker_path(tmp_path: Path, session_id: str = SESSION_ID) -> Path:
    return tmp_path / ".claude" / ".handoff-nudge-fired.d" / f"{session_id}-drift"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNudgeHandoffNearContextCap:
    def test_below_threshold_is_silent(self, tmp_path):
        """Token sum below 120 000: no stdout, no marker, no log (skip line was removed)."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_record(input_tok=30000, output_tok=20000)])
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert not _marker_path(tmp_path).exists()
        # Below threshold: hook exits silently with no log output.
        assert not _log_path(tmp_path).exists()

    def test_above_threshold_fires_nudge(self, tmp_path):
        """Token sum >= threshold, spread across all four usage fields, fires: JSON emitted, marker created, log has 'nudged'."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(
            transcript,
            [_assistant_record(cache_read=580000, cache_create=20000, input_tok=30000, output_tok=20000)],
        )
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() != ""
        payload = json.loads(result.stdout)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        assert "handoff" in ctx.lower() or "/handoff" in ctx
        assert _marker_path(tmp_path).exists()
        log = _log_path(tmp_path)
        assert "nudged" in log.read_text()
        assert f"est={ABOVE_LARGE}" in log.read_text()

    def test_already_fired_is_silent(self, tmp_path):
        """When the per-session marker already exists, subsequent calls produce no stdout."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(ABOVE_LARGE)])
        marker_dir = tmp_path / ".claude" / ".handoff-nudge-fired.d"
        marker_dir.mkdir(parents=True)
        _marker_path(tmp_path).touch()
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_killswitch_suppresses(self, tmp_path):
        """Presence of ~/.claude/.handoff-nudge-disabled suppresses nudge and produces no log line."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(ABOVE_LARGE)])
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir(parents=True)
        (claude_dir / ".handoff-nudge-disabled").touch()
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        # Kill-switch exits before logging.
        assert not _log_path(tmp_path).exists()

    def test_subagent_gate(self, tmp_path):
        """Payload with agent_type field is silently ignored — nudge is parent-session-only."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(ABOVE_LARGE)])
        payload = _base_payload(transcript)
        payload["agent_type"] = "code-writer"
        result = _run_hook(payload, tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_plan_mode_gate(self, tmp_path):
        """permission_mode == 'plan' is silently ignored."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(ABOVE_LARGE)])
        payload = _base_payload(transcript)
        payload["permission_mode"] = "plan"
        result = _run_hook(payload, tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_missing_transcript_path_field(self, tmp_path):
        """Payload with no transcript_path key exits silently."""
        payload = {"session_id": SESSION_ID}
        result = _run_hook(payload, tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_transcript_file_absent(self, tmp_path):
        """transcript_path pointing at a nonexistent file exits silently."""
        payload = {
            "session_id": SESSION_ID,
            "transcript_path": str(tmp_path / "nonexistent.jsonl"),
        }
        result = _run_hook(payload, tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_fresh_session_no_usage_block(self, tmp_path):
        """Transcript with no assistant records that have usage exits silently."""
        transcript = tmp_path / "t.jsonl"
        # Only a user-type record — no assistant usage block.
        _write_transcript(transcript, [{"type": "user", "message": {"content": "hello"}}])
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_multi_entry_turn_uses_latest_usage(self, tmp_path):
        """When transcript has multiple assistant records, the last one with usage governs."""
        transcript = tmp_path / "t.jsonl"
        # First record: well below threshold (50k total).
        # Second record: above threshold (ABOVE_LARGE total).
        _write_transcript(
            transcript,
            [
                _assistant_record(input_tok=30000, output_tok=20000),
                _record_totalling(ABOVE_LARGE),
            ],
        )
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() != ""
        payload = json.loads(result.stdout)
        assert "additionalContext" in payload["hookSpecificOutput"]

    def test_schema_drift_logs_and_exits(self, tmp_path):
        """Usage block present with all token fields 0/null: log schema-drift, exit 0, no nudge marker."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_record()])  # all fields default to 0
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert not _marker_path(tmp_path).exists()
        log = _log_path(tmp_path)
        assert log.exists()
        assert "schema-drift" in log.read_text()
        assert _drift_marker_path(tmp_path).exists()

    def test_schema_drift_only_logs_once_per_session(self, tmp_path):
        """Repeated calls with all-zero usage only write one schema-drift log line."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_record()])
        _run_hook(_base_payload(transcript), tmp_path)
        _run_hook(_base_payload(transcript), tmp_path)
        log = _log_path(tmp_path)
        drift_lines = [ln for ln in log.read_text().splitlines() if "schema-drift" in ln]
        assert len(drift_lines) == 1

    def test_malformed_jsonl_is_silent(self, tmp_path):
        """Transcript file with invalid JSON lines exits silently without crashing."""
        transcript = tmp_path / "t.jsonl"
        transcript.write_text("this is not json\nalso not json\n{broken\n")
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_unwritable_marker_dir_is_silent(self, tmp_path):
        """If the marker directory is unwritable, the hook exits 0 without crashing."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(ABOVE_LARGE)])
        # Create the marker dir with no write permission.
        marker_dir = tmp_path / ".claude" / ".handoff-nudge-fired.d"
        marker_dir.mkdir(parents=True)
        marker_dir.chmod(0o555)
        try:
            result = _run_hook(_base_payload(transcript), tmp_path)
            assert result.returncode == 0
            # Marker write failed, but nudge JSON should still have been emitted.
            assert result.stdout.strip() != ""
            payload = json.loads(result.stdout)
            assert "additionalContext" in payload["hookSpecificOutput"]
        finally:
            # Restore permissions so pytest cleanup can delete tmp_path.
            marker_dir.chmod(0o755)

    def test_traversal_session_id_does_not_create_file_outside_marker_dir(self, tmp_path):
        """A session_id containing '../' must not let the fire-path `touch
        "$FIRED_MARKER"` escape .handoff-nudge-fired.d/.

        Deliberately does NOT plant a canary at the traversal target first:
        FIRED_MARKER is both the "already fired" existence check and the
        write target at the identical path, so a pre-planted canary would
        make the check true and suppress the fire — collapsing guard-present
        (never reaches the check) and guard-absent (reaches it, finds
        "already fired", suppresses) onto the same observable outcome. What
        discriminates instead: whether a file is newly created outside the
        marker directory at all. With an above-threshold transcript and no
        pre-existing marker, the guard blocks before FIRED_MARKER is even
        built; without it, the touch would create ~/.claude/canary."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(ABOVE_LARGE)])
        canary_path = tmp_path / ".claude" / "canary"

        result = _run_hook(
            _base_payload(transcript, session_id=TRAVERSAL_SESSION_ID), tmp_path
        )

        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert not canary_path.exists(), (
            "a traversal session_id must not create a file outside .handoff-nudge-fired.d/"
        )

    def test_latency_under_500ms(self, tmp_path):
        """Hook completes in under 500ms even with a ~10 MB transcript (10000 valid JSONL lines).

        The tail -n 200 in the hook prevents the read from being O(file_size),
        so runtime is dominated by shell+jq startup (~25-50ms), not file size.
        The 500ms bound guards against an accidental regression to full-file reads
        while tolerating CI-runner jitter (bash+jq startup can reach 150-200ms
        under memory pressure on loaded runners).
        """
        transcript = tmp_path / "large.jsonl"
        single_line = json.dumps(_assistant_record(input_tok=5000, output_tok=1000))
        transcript.write_text("\n".join([single_line] * 10000) + "\n")
        payload = _base_payload(transcript)
        start = time.perf_counter()
        result = _run_hook(payload, tmp_path)
        elapsed = time.perf_counter() - start
        assert result.returncode == 0
        assert elapsed < 0.500, f"Hook took {elapsed:.3f}s — expected < 500ms (tail -n 200 should prevent O(file) reads)"

    def test_partial_usage_block_falls_to_below_threshold(self, tmp_path):
        """A usage block with only output_tokens present sums to 500 — below threshold. No schema-drift, no nudge."""
        transcript = tmp_path / "t.jsonl"
        record = {
            "type": "assistant",
            "message": {
                "usage": {"output_tokens": 500}
            },
        }
        _write_transcript(transcript, [record])
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        # No log: below threshold produces no log line (skip line was removed; schema-drift not triggered).
        assert not _log_path(tmp_path).exists()

    def test_empty_json_object_input_is_fail_open(self, tmp_path):
        """Empty JSON object ({}) as INPUT: exit 0, no blocking stdout.

        Verifies the four-read rewrite preserves fail-open semantics when
        session_id is absent — the hook exits silently without emitting any
        output that could block a user prompt.
        """
        result = subprocess.run(
            [str(NUDGE_HOOK)],
            input="{}",
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(tmp_path)},
            check=False,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_malformed_input_is_fail_open(self, tmp_path):
        """Non-JSON string as INPUT: exit 0, no blocking stdout.

        Verifies the four-read rewrite preserves the || true fail-open guard
        when jq cannot parse the input — the hook exits silently rather than
        crashing or emitting output that could block a user prompt.
        """
        result = subprocess.run(
            [str(NUDGE_HOOK)],
            input="not json at all",
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(tmp_path)},
            check=False,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    # -----------------------------------------------------------------------
    # Per-model context-window resolution (GH-556)
    # -----------------------------------------------------------------------

    @pytest.mark.parametrize("model,window", KNOWN_MODEL_WINDOWS)
    def test_fires_at_exactly_threshold_for_model(self, tmp_path, model, window):
        """Each known model ID fires when its own 60%-of-window threshold is met exactly."""
        threshold = window * 60 // 100
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(threshold, model=model)])
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() != ""

    @pytest.mark.parametrize("model,window", KNOWN_MODEL_WINDOWS)
    def test_silent_one_below_threshold_for_model(self, tmp_path, model, window):
        """Each known model ID stays silent one token below its own threshold."""
        threshold = window * 60 // 100
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(threshold - 1, model=model)])
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_old_120k_constant_no_longer_fires_on_1m_models(self, tmp_path):
        """135 000 (fired under the old flat 120 000 constant) is now silent on a 1M model. Direct GH-556 regression test."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(135000, model="claude-sonnet-5")])
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    @pytest.mark.parametrize("total,expect_fire", [(LARGE_THRESHOLD - 1, False), (LARGE_THRESHOLD, True)])
    def test_missing_model_key_takes_1m_default(self, tmp_path, total, expect_fire):
        """A record with no "model" key at all resolves to the 1M default window."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(total, model=None)])
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert (result.stdout.strip() != "") == expect_fire

    @pytest.mark.parametrize("total,expect_fire", [(LARGE_THRESHOLD - 1, False), (LARGE_THRESHOLD, True)])
    def test_null_model_takes_1m_default(self, tmp_path, total, expect_fire):
        """A record with an explicit "model": null resolves to the 1M default window."""
        transcript = tmp_path / "t.jsonl"
        record = _record_totalling(total)
        record["message"]["model"] = None
        _write_transcript(transcript, [record])
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert (result.stdout.strip() != "") == expect_fire

    @pytest.mark.parametrize("total,expect_fire", [(LARGE_THRESHOLD - 1, False), (LARGE_THRESHOLD, True)])
    def test_unrecognized_model_id_takes_1m_default(self, tmp_path, total, expect_fire):
        """An unrecognized/future model ID resolves to the 1M default window."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(total, model="claude-future-model-9")])
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert (result.stdout.strip() != "") == expect_fire

    def test_dated_snapshot_suffix_resolves_same_window_as_dateless(self, tmp_path):
        """A dated-snapshot-suffix model ID resolves to the same window as its dateless form."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(LARGE_THRESHOLD, model="claude-sonnet-5-20260601")])
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() != ""

    def test_dated_snapshot_of_200k_arm_model_still_resolves_small_window(self, tmp_path):
        """A dated-snapshot suffix on a 200k-arm model ID still matches that arm's glob, not the 1M default.

        Distinct from the dateless-catchall test above: claude-sonnet-5 already
        falls to the 1M default regardless of glob correctness, so it can't
        catch an over-anchored fix that drops the trailing-"-*" wildcard
        entirely. This id must match `claude-opus-4-1|claude-opus-4-1-*`.
        """
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(SMALL_THRESHOLD, model="claude-opus-4-1-20260301")])
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() != ""

    @pytest.mark.parametrize("model_id", COLLIDING_MODEL_IDS)
    def test_longer_numeral_does_not_collide_with_200k_arm_prefix(self, tmp_path, model_id):
        """A model ID sharing a known 200k arm's literal prefix but extending it with more digits
        (claude-opus-4-10 vs the claude-opus-4-1 arm) must not match that arm — it should fall to
        the 1M default. Direct regression pin for the case-glob prefix-collision bug: the fix
        anchors each arm to an exact match or a trailing "-", not a bare trailing "*".
        """
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(LARGE_THRESHOLD - 1, model=model_id)])
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == "", (
            f"{model_id} matched a 200k arm by string-prefix collision instead of falling to the 1M default"
        )

    def test_nudged_log_line_includes_model_and_window(self, tmp_path):
        """The 'nudged' log line records the resolved model and window alongside est=."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(LARGE_THRESHOLD, model="claude-sonnet-5")])
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        log_text = _log_path(tmp_path).read_text()
        assert "model=claude-sonnet-5" in log_text
        assert f"window={LARGE_WINDOW}" in log_text

    def test_injected_context_has_no_cost_percentage_claim(self, tmp_path):
        """The injected additionalContext string no longer asserts an unsourced cost-savings percentage."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(ABOVE_LARGE)])
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        assert "25%" not in ctx

    def test_synthetic_model_all_zero_usage_takes_schema_drift_path(self, tmp_path):
        """A <synthetic> model with all-zero usage still takes the schema-drift path, not the window/threshold path."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_record(model="<synthetic>")])
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        log = _log_path(tmp_path)
        assert "schema-drift" in log.read_text()
