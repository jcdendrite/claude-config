"""Tests for nudge-error-mode-analysis.sh.

The hook is a UserPromptSubmit hook that emits a one-shot
hookSpecificOutput.additionalContext JSON payload when the current
session's transcript-analysis.py `friction-count` composite (hook denials +
failed test runs + user-correction phrases) reaches FRICTION_THRESHOLD (12,
backtested — see the hook's own comment and the PR description for the
distribution). The nudge fires once per session — a marker file gates
subsequent turns, and that marker check runs *before* python3 is ever
spawned.

The hook is opt-in: dormant unless ~/.claude/.error-mode-nudge-enabled
exists. `_run_hook`'s `enabled=True` default arms that marker for every
test that exercises gates past the opt-in check; only the test verifying
the dormant-by-default behavior itself passes `enabled=False`.

All tests sandbox $HOME via monkeypatch so markers and logs land in tmp_path
rather than the real $HOME. The real transcript-analysis.py script is
symlinked into the sandboxed $HOME/.claude/scripts/ so the hook's own
`$HOME/.claude/scripts/transcript-analysis.py` reference resolves.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from helpers import HOOKS_DIR, SCRIPTS_DIR, TRAVERSAL_SESSION_ID

NUDGE_HOOK = HOOKS_DIR / "nudge-error-mode-analysis.sh"

SESSION_ID = "test-session-friction-001"
FRICTION_THRESHOLD = 12  # mirrors the hook's own FRICTION_THRESHOLD constant

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _denial_record(tool_id: str) -> dict:
    """A current-format hook-denial record: an is_error tool_result whose text
    matches transcript-analysis.py's _HOOK_DENIAL_SIGNATURE."""
    return {
        "type": "user",
        "message": {"content": [
            {
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": "Commit blocked by code-review gate.",
                "is_error": True,
            },
        ]},
    }


def _write_transcript(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def _write_denial_transcript(path: Path, count: int) -> None:
    """Write a transcript with exactly `count` distinct-tool_use_id denials —
    each denial contributes exactly 1 to the friction-count composite."""
    _write_transcript(path, [_denial_record(f"toolu_{i}") for i in range(count)])


def _prepare_home(home: Path) -> None:
    """Symlink the real transcript-analysis.py into the sandboxed $HOME so the
    hook's `$HOME/.claude/scripts/transcript-analysis.py` reference resolves."""
    scripts_dir = home / ".claude" / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    link = scripts_dir / "transcript-analysis.py"
    if not link.exists():
        link.symlink_to(SCRIPTS_DIR / "transcript-analysis.py")


def _run_hook(
    payload: dict, tmp_path: Path, *, extra_env: dict | None = None, enabled: bool = True
) -> subprocess.CompletedProcess:
    """Run the hook against a sandboxed $HOME. `enabled=True` (the default) arms
    the opt-in marker first, since most tests exercise gates *past* the opt-in
    check; pass `enabled=False` to test the dormant-by-default behavior itself."""
    _prepare_home(tmp_path)
    if enabled:
        _enable_nudge(tmp_path)
    env = _sandboxed_env(tmp_path)
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


def _base_payload(transcript_path: Path, session_id: str = SESSION_ID) -> dict:
    return {
        "session_id": session_id,
        "transcript_path": str(transcript_path),
    }


def _log_path(tmp_path: Path) -> Path:
    return tmp_path / ".claude" / ".error-mode-nudge.log"


def _marker_path(tmp_path: Path, session_id: str = SESSION_ID) -> Path:
    return tmp_path / ".claude" / ".error-mode-nudge-fired.d" / session_id


def _enable_nudge(tmp_path: Path) -> None:
    """Arm the opt-in nudge hook: touch ~/.claude/.error-mode-nudge-enabled."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    (claude_dir / ".error-mode-nudge-enabled").touch()


def _prepare_config_dir(config_dir: Path) -> None:
    """Symlink the real transcript-analysis.py into an arbitrary config dir and
    arm the opt-in marker there, for CLAUDE_CONFIG_DIR-set cases that must not
    touch $HOME/.claude at all."""
    scripts_dir = config_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    link = scripts_dir / "transcript-analysis.py"
    if not link.exists():
        link.symlink_to(SCRIPTS_DIR / "transcript-analysis.py")
    (config_dir / ".error-mode-nudge-enabled").touch()


def _sandboxed_env(tmp_path: Path) -> dict:
    """$HOME sandboxed to tmp_path, with any real CLAUDE_CONFIG_DIR cleared so
    the hook resolves paths under the sandbox rather than the real config dir."""
    env = {**os.environ, "HOME": str(tmp_path)}
    env.pop("CLAUDE_CONFIG_DIR", None)
    return env


def _fake_bin_dir(tmp_path: Path, name: str) -> Path:
    d = tmp_path / f"fakebin-{name}"
    d.mkdir(exist_ok=True)
    return d


def _restricted_path_without_python3(fake_bin: Path) -> str:
    """Build a PATH containing only a symlinked-tool directory that has every
    binary the hook needs except python3, so `command -v python3` fails."""
    for tool in ("cat", "jq", "mkdir", "find", "touch", "timeout"):
        real = Path("/usr/bin") / tool
        if real.exists():
            (fake_bin / tool).symlink_to(real)
    return str(fake_bin)


def _fake_python3(fake_bin: Path, script_body: str) -> str:
    """Write a fake `python3` shim into fake_bin and return a PATH that finds
    it ahead of the real python3."""
    shim = fake_bin / "python3"
    shim.write_text(script_body)
    shim.chmod(0o755)
    return f"{fake_bin}:{os.environ['PATH']}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNudgeErrorModeAnalysis:
    def test_below_threshold_is_silent(self, tmp_path):
        """Composite below FRICTION_THRESHOLD: no stdout, no marker, no log."""
        transcript = tmp_path / "t.jsonl"
        _write_denial_transcript(transcript, FRICTION_THRESHOLD - 1)
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert not _marker_path(tmp_path).exists()
        assert not _log_path(tmp_path).exists()

    def test_at_threshold_fires_nudge(self, tmp_path):
        """Composite exactly at FRICTION_THRESHOLD fires: JSON emitted, marker
        created, log has 'nudged'."""
        transcript = tmp_path / "t.jsonl"
        _write_denial_transcript(transcript, FRICTION_THRESHOLD)
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() != ""
        payload = json.loads(result.stdout)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        assert "/error-mode-analysis" in ctx
        assert _marker_path(tmp_path).exists()
        log = _log_path(tmp_path)
        assert "nudged" in log.read_text()
        assert f"friction={FRICTION_THRESHOLD}" in log.read_text()

    def test_honors_claude_config_dir_for_markers_log_and_script_invocation(self, tmp_path):
        """CLAUDE_CONFIG_DIR set to a directory outside $HOME/.claude: the
        opt-in marker, fired marker, log, and the transcript-analysis.py
        invocation itself all resolve under it instead of $HOME/.claude."""
        config_dir = tmp_path / "alt-config"
        _prepare_config_dir(config_dir)
        transcript = tmp_path / "t.jsonl"
        _write_denial_transcript(transcript, FRICTION_THRESHOLD)

        result = _run_hook(
            _base_payload(transcript),
            tmp_path,
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            enabled=False,
        )
        assert result.returncode == 0
        assert result.stdout.strip() != ""
        payload = json.loads(result.stdout)
        assert "/error-mode-analysis" in payload["hookSpecificOutput"]["additionalContext"]
        assert (config_dir / ".error-mode-nudge-fired.d" / SESSION_ID).exists()
        assert "nudged" in (config_dir / ".error-mode-nudge.log").read_text()
        assert not (tmp_path / ".claude" / ".error-mode-nudge-fired.d").exists()

    def test_unresolvable_config_dir_is_silent_and_python3_not_spawned(self, tmp_path):
        """CLAUDE_CONFIG_DIR set to a relative value (unresolvable per
        _lib_config_dir) fails open: exit 0, no stdout, and python3 is never
        spawned — the config-dir resolution gates the spawn just like the
        fired-marker and opt-in gates do."""
        transcript = tmp_path / "t.jsonl"
        _write_denial_transcript(transcript, FRICTION_THRESHOLD)
        fake_bin = _fake_bin_dir(tmp_path, "unresolvable-config-dir")
        spawn_counter = tmp_path / "python3-spawn-count"
        shim_path = _fake_python3(
            fake_bin,
            f"#!/bin/bash\necho invoked >> {spawn_counter}\nexit 0\n",
        )
        result = _run_hook(
            _base_payload(transcript),
            tmp_path,
            extra_env={"CLAUDE_CONFIG_DIR": "relative/path", "PATH": shim_path},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert not spawn_counter.exists(), (
            "python3 must not be spawned when _lib_config_dir cannot resolve CLAUDE_CONFIG_DIR"
        )

    def test_one_below_threshold_stays_quiet_threshold_itself_fires(self, tmp_path):
        """Exact boundary: FRICTION_THRESHOLD - 1 is silent; FRICTION_THRESHOLD fires."""
        quiet_transcript = tmp_path / "quiet.jsonl"
        _write_denial_transcript(quiet_transcript, FRICTION_THRESHOLD - 1)
        quiet_result = _run_hook(
            {"session_id": "quiet-session", "transcript_path": str(quiet_transcript)}, tmp_path
        )
        assert quiet_result.stdout.strip() == ""

        fires_transcript = tmp_path / "fires.jsonl"
        _write_denial_transcript(fires_transcript, FRICTION_THRESHOLD)
        fires_result = _run_hook(
            {"session_id": "fires-session", "transcript_path": str(fires_transcript)}, tmp_path
        )
        assert fires_result.stdout.strip() != ""

    def test_already_fired_is_silent_and_python3_not_spawned(self, tmp_path):
        """When the per-session marker already exists, the hook produces no
        stdout AND never invokes python3 — the marker check gates the spawn
        itself, not just the emitted output (gate order: marker before preflight)."""
        transcript = tmp_path / "t.jsonl"
        _write_denial_transcript(transcript, FRICTION_THRESHOLD)
        marker_dir = tmp_path / ".claude" / ".error-mode-nudge-fired.d"
        marker_dir.mkdir(parents=True)
        _marker_path(tmp_path).touch()

        fake_bin = _fake_bin_dir(tmp_path, "spawn-check")
        spawn_counter = tmp_path / "python3-spawn-count"
        shim_path = _fake_python3(
            fake_bin,
            f"#!/bin/bash\necho invoked >> {spawn_counter}\nexit 0\n",
        )

        result = _run_hook(_base_payload(transcript), tmp_path, extra_env={"PATH": shim_path})
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert not spawn_counter.exists(), "python3 shim was invoked despite an existing fired-marker"

    def test_disabled_by_default_without_opt_in_marker(self, tmp_path):
        """Absence of ~/.claude/.error-mode-nudge-enabled suppresses the nudge —
        opt-in, not opt-out — even when the composite is at threshold, and
        produces no log line. Also asserts python3 is never spawned: the gate
        must short-circuit *before* any transcript work, the same invariant
        `test_already_fired_is_silent_and_python3_not_spawned` pins for the
        fired-marker gate, and this is gate #1 — the cheapest one to prove."""
        transcript = tmp_path / "t.jsonl"
        _write_denial_transcript(transcript, FRICTION_THRESHOLD)

        fake_bin = _fake_bin_dir(tmp_path, "opt-in-spawn-check")
        spawn_counter = tmp_path / "python3-spawn-count"
        shim_path = _fake_python3(
            fake_bin,
            f"#!/bin/bash\necho invoked >> {spawn_counter}\nexit 0\n",
        )

        result = _run_hook(
            _base_payload(transcript), tmp_path, enabled=False, extra_env={"PATH": shim_path}
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert not _log_path(tmp_path).exists()
        assert not _marker_path(tmp_path).exists()
        assert not spawn_counter.exists(), "python3 shim was invoked despite the opt-in marker being absent"

    def test_subagent_gate(self, tmp_path):
        """Payload with agent_type field is silently ignored — nudge is
        parent-session-only."""
        transcript = tmp_path / "t.jsonl"
        _write_denial_transcript(transcript, FRICTION_THRESHOLD)
        payload = _base_payload(transcript)
        payload["agent_type"] = "code-writer"
        result = _run_hook(payload, tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_plan_mode_gate(self, tmp_path):
        """permission_mode == 'plan' is silently ignored."""
        transcript = tmp_path / "t.jsonl"
        _write_denial_transcript(transcript, FRICTION_THRESHOLD)
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

    def test_missing_session_id_field(self, tmp_path):
        """Payload with no session_id key exits silently."""
        transcript = tmp_path / "t.jsonl"
        _write_denial_transcript(transcript, FRICTION_THRESHOLD)
        payload = {"transcript_path": str(transcript)}
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

    def test_python3_missing_is_silent(self, tmp_path):
        """No python3 on PATH: hook exits 0 with no stdout (fail-open preflight)."""
        transcript = tmp_path / "t.jsonl"
        _write_denial_transcript(transcript, FRICTION_THRESHOLD)
        fake_bin = _fake_bin_dir(tmp_path, "no-python3")
        restricted_path = _restricted_path_without_python3(fake_bin)
        result = _run_hook(_base_payload(transcript), tmp_path, extra_env={"PATH": restricted_path})
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_python3_too_old_is_silent(self, tmp_path):
        """python3 present but reporting < 3.11 via the version preflight:
        hook exits 0 with no stdout."""
        transcript = tmp_path / "t.jsonl"
        _write_denial_transcript(transcript, FRICTION_THRESHOLD)
        fake_bin = _fake_bin_dir(tmp_path, "old-python3")
        shim_path = _fake_python3(
            fake_bin,
            '#!/bin/bash\n[ "$1" = "-c" ] && exit 1\nexit 1\n',
        )
        result = _run_hook(_base_payload(transcript), tmp_path, extra_env={"PATH": shim_path})
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_checkpoint_flag_passed_with_session_scoped_path(self, tmp_path):
        """--checkpoint <session-scoped-path> is included on every friction-count
        invocation (not just on fire), so the incremental scan persists across
        hook fires within a session. Uses a python3 shim that records its
        argv to a file — a below-threshold transcript so the invocation is
        exercised on the common, every-prompt path."""
        transcript = tmp_path / "t.jsonl"
        _write_denial_transcript(transcript, FRICTION_THRESHOLD - 1)
        fake_bin = _fake_bin_dir(tmp_path, "checkpoint-args")
        invocation_log = tmp_path / "python3-invocation-args"
        shim_path = _fake_python3(
            fake_bin,
            f'#!/bin/bash\n'
            f'[ "$1" = "-c" ] && exit 0\n'
            f'echo "$@" >> {invocation_log}\n'
            f'echo 0\n',
        )
        result = _run_hook(_base_payload(transcript), tmp_path, extra_env={"PATH": shim_path})
        assert result.returncode == 0
        invocation = invocation_log.read_text()
        assert "--checkpoint" in invocation
        checkpoint_dir = tmp_path / ".claude" / ".error-mode-nudge-checkpoint.d"
        expected_checkpoint_path = str(checkpoint_dir / SESSION_ID)
        assert expected_checkpoint_path in invocation
        assert checkpoint_dir.is_dir()

    def test_real_python3_checkpoint_round_trip_across_two_hook_invocations(self, tmp_path):
        """Two real (non-shimmed) hook invocations for the same session,
        against a transcript that grows between calls: the second
        invocation's fire/no-fire decision must reflect the *cumulative*
        composite (this call's delta plus the first call's checkpointed
        totals), not just this call's own delta. This is the actual bash ->
        real python3 -> transcript-analysis.py -> checkpoint file -> second
        bash invocation round trip; the other checkpoint tests either shim
        python3 (argv-passing only) or call cmd_friction_count directly
        (no subprocess boundary)."""
        session_id = "round-trip-session"
        transcript = tmp_path / "t.jsonl"
        _write_denial_transcript(transcript, FRICTION_THRESHOLD - 1)

        first_result = _run_hook({"session_id": session_id, "transcript_path": str(transcript)}, tmp_path)
        assert first_result.returncode == 0
        assert first_result.stdout.strip() == "", "below-threshold first call must stay quiet"
        assert not _marker_path(tmp_path, session_id).exists()

        checkpoint_file = tmp_path / ".claude" / ".error-mode-nudge-checkpoint.d" / session_id
        assert checkpoint_file.exists(), "first invocation must persist a checkpoint for the second to read"
        first_state = json.loads(checkpoint_file.read_text())
        assert first_state["totals"]["denials"] == FRICTION_THRESHOLD - 1

        with transcript.open("a") as fh:
            fh.write(json.dumps(_denial_record(f"toolu_{FRICTION_THRESHOLD - 1}")) + "\n")

        second_result = _run_hook({"session_id": session_id, "transcript_path": str(transcript)}, tmp_path)
        assert second_result.returncode == 0
        assert second_result.stdout.strip() != "", (
            "second call's new denial pushes the cumulative composite to FRICTION_THRESHOLD; must fire"
        )
        payload = json.loads(second_result.stdout)
        assert "/error-mode-analysis" in payload["hookSpecificOutput"]["additionalContext"]
        assert _marker_path(tmp_path, session_id).exists()

    def test_non_integer_stdout_is_silent(self, tmp_path):
        """friction-count producing non-integer stdout (simulated via a python3
        shim that passes the version preflight but emits garbage otherwise):
        hook exits 0 with no stdout."""
        transcript = tmp_path / "t.jsonl"
        _write_denial_transcript(transcript, FRICTION_THRESHOLD)
        fake_bin = _fake_bin_dir(tmp_path, "garbage-output")
        shim_path = _fake_python3(
            fake_bin,
            '#!/bin/bash\n[ "$1" = "-c" ] && exit 0\necho "not-a-number"\nexit 0\n',
        )
        result = _run_hook(_base_payload(transcript), tmp_path, extra_env={"PATH": shim_path})
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_friction_count_timeout_is_silent(self, tmp_path):
        """friction-count hanging past the timeout wrapper: hook exits 0 with
        no stdout. Uses a python3 shim that sleeps past the hook's 10s
        `timeout` wrapper — this test intentionally runs for ~10s."""
        transcript = tmp_path / "t.jsonl"
        _write_denial_transcript(transcript, FRICTION_THRESHOLD)
        fake_bin = _fake_bin_dir(tmp_path, "hangs")
        shim_path = _fake_python3(
            fake_bin,
            '#!/bin/bash\n[ "$1" = "-c" ] && exit 0\nsleep 15\necho 99\n',
        )
        result = _run_hook(_base_payload(transcript), tmp_path, extra_env={"PATH": shim_path})
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_malformed_jsonl_is_silent(self, tmp_path):
        """Transcript file with invalid JSON lines exits silently without crashing."""
        transcript = tmp_path / "t.jsonl"
        transcript.write_text("this is not json\nalso not json\n{broken\n")
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_empty_transcript_is_silent(self, tmp_path):
        """A transcript with zero friction signals stays quiet."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [{"type": "user", "message": {"content": "hello"}}])
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    @pytest.mark.skipif(
        os.geteuid() == 0,
        reason="root ignores chmod permission bits, so this test's forced "
        "write-failure setup would not actually block the marker write",
    )
    def test_unwritable_marker_dir_is_silent(self, tmp_path):
        """If the marker directory is unwritable, the hook exits 0 without
        crashing, and the marker write itself is confirmed to have failed
        (not just that the hook tolerated some other failure)."""
        transcript = tmp_path / "t.jsonl"
        _write_denial_transcript(transcript, FRICTION_THRESHOLD)
        marker_dir = tmp_path / ".claude" / ".error-mode-nudge-fired.d"
        marker_dir.mkdir(parents=True)
        marker_dir.chmod(0o555)
        try:
            result = _run_hook(_base_payload(transcript), tmp_path)
            assert result.returncode == 0
            assert not _marker_path(tmp_path).exists(), (
                "marker write should have failed against the unwritable directory"
            )
            # Marker write failed, but nudge JSON should still have been emitted.
            assert result.stdout.strip() != ""
            payload = json.loads(result.stdout)
            assert "additionalContext" in payload["hookSpecificOutput"]
        finally:
            marker_dir.chmod(0o755)

    def test_empty_json_object_input_is_fail_open(self, tmp_path):
        """Empty JSON object ({}) as INPUT: exit 0, no blocking stdout."""
        _prepare_home(tmp_path)
        _enable_nudge(tmp_path)
        result = subprocess.run(
            [str(NUDGE_HOOK)],
            input="{}",
            capture_output=True,
            text=True,
            env=_sandboxed_env(tmp_path),
            check=False,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_malformed_input_is_fail_open(self, tmp_path):
        """Non-JSON string as INPUT: exit 0, no blocking stdout."""
        _prepare_home(tmp_path)
        _enable_nudge(tmp_path)
        result = subprocess.run(
            [str(NUDGE_HOOK)],
            input="not json at all",
            capture_output=True,
            text=True,
            env=_sandboxed_env(tmp_path),
            check=False,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_always_exits_zero_even_on_fire(self, tmp_path):
        """Sanity: a firing nudge still exits 0 (never blocks the prompt)."""
        transcript = tmp_path / "t.jsonl"
        _write_denial_transcript(transcript, FRICTION_THRESHOLD + 4)
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0

    def test_traversal_session_id_blocks_before_reaching_checkpoint_write(self, tmp_path):
        """A session_id shaped like a path traversal must be rejected before
        the hook ever builds a --checkpoint argument from it.

        FIRED_MARKER (.error-mode-nudge-fired.d/$SESSION_ID) and
        CHECKPOINT_FILE (.error-mode-nudge-checkpoint.d/$SESSION_ID) are
        sibling directories at the same depth under ~/.claude, so a
        traversing id resolves both to the same file. Planting a canary at
        that resolved path to protect against the checkpoint write would
        also make the FIRED_MARKER "already fired" check true — so the hook
        would suppress and exit before spawning python3 whether or not the
        guard ran, and no assertion could separate the two cases (the same
        collapse test_require_routing_read.py documents).

        What DOES discriminate: without the guard this session_id reaches
        step 7 and spawns python3 with a --checkpoint path pointing outside
        .error-mode-nudge-checkpoint.d/ (the traversal target, where
        friction-count would then write); with the guard, the hook exits at
        the validation step and python3 is never spawned at all — the same
        gate-before-spawn property test_already_fired_is_silent_and_python3_not_spawned
        pins for the fired-marker gate."""
        transcript = tmp_path / "t.jsonl"
        _write_denial_transcript(transcript, FRICTION_THRESHOLD)
        fake_bin = _fake_bin_dir(tmp_path, "traversal-spawn-check")
        spawn_counter = tmp_path / "python3-spawn-count"
        shim_path = _fake_python3(
            fake_bin,
            f"#!/bin/bash\necho invoked >> {spawn_counter}\nexit 0\n",
        )
        payload = _base_payload(transcript, session_id=TRAVERSAL_SESSION_ID)
        result = _run_hook(payload, tmp_path, extra_env={"PATH": shim_path})
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert not spawn_counter.exists(), (
            "python3 must not be spawned for a path-traversal session_id"
        )

    def test_latency_under_2s_for_realistic_transcript(self, tmp_path):
        """Hook completes well under the 10s timeout wrapper for a transcript far
        larger than any real one measured on the implementing machine (see the
        hook's own comment: 6.4 MB / 2656 lines -> ~0.18s; 64 MB / 26560 lines ->
        ~1.3s). This transcript has 3000 lines of below-threshold denials."""
        transcript = tmp_path / "large.jsonl"
        _write_denial_transcript(transcript, FRICTION_THRESHOLD - 1)
        extra_lines = [json.dumps({"type": "assistant", "message": {"content": []}})] * 3000
        with transcript.open("a") as fh:
            fh.write("\n".join(extra_lines) + "\n")
        start = time.perf_counter()
        result = _run_hook(_base_payload(transcript), tmp_path)
        elapsed = time.perf_counter() - start
        assert result.returncode == 0
        assert elapsed < 2.0, f"Hook took {elapsed:.3f}s — expected < 2s for a moderate transcript"
