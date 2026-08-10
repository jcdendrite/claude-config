"""Tests for nudge-handoff-near-context-cap.sh.

The hook is a UserPromptSubmit and Stop hook that emits a one-shot
hookSpecificOutput.additionalContext JSON payload when the estimated carried
token count crosses the lesser of 40% of the resolved model's context window
or an absolute-token cap (HANDOFF_NUDGE_ABS_CAP, default 360000). 200k models
stay at 80000 (below the default cap, unaffected); 1M models — including
unrecognized/missing model IDs, which default to the 1M window — are capped
at 360000 rather than the raw 400000 (40% of 1M). The nudge fires once per
session — a marker file gates subsequent turns.

All tests sandbox $HOME via monkeypatch so markers and logs land in tmp_path
rather than the real $HOME.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
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

# Both events the hook is registered under; used only where the assertions
# differ per event (the emitted hookEventName and event= log field) — gate
# logic upstream of the event read (kill-switch, subagent, plan-mode,
# already-fired) doesn't branch on it, so those tests stay unparametrized.
HOOK_EVENT_NAMES = ["UserPromptSubmit", "Stop"]

# Mirrors the hook's own window table so no test hand-computes a threshold.
LARGE_WINDOW = 1_000_000
SMALL_WINDOW = 200_000
SMALL_THRESHOLD = 80_000  # 40% of 200k — the pct arm, unaffected by the cap (A7)
DEFAULT_ABS_CAP = 360_000  # HANDOFF_NUDGE_ABS_CAP's shipped default
# Effective 200k-window threshold under min(pct, cap) — a distinct symbol from
# SMALL_THRESHOLD (currently equal to it per A7) so a future cap below 80,000
# doesn't silently overload one name with two meanings.
EFFECTIVE_SMALL_THRESHOLD = SMALL_THRESHOLD
# Effective 1M-window threshold under min(pct, cap): 40% of 1M is 400,000,
# above the cap, so the cap itself governs (see the collision-probe
# re-derivation for why the probe below must not be parameterized on this).
LARGE_THRESHOLD = DEFAULT_ABS_CAP
ABOVE_LARGE = 650_000

# The collision probe below only discriminates a correct case-glob arm from a
# broken one when the cap sits strictly above SMALL_THRESHOLD (or the buggy
# and correct arms collapse to the same value) and at or below 40% of 1M (or
# the correct arm also fires at cap-1, a false red) — see the collision-probe
# re-derivation. Fails loudly at collection time rather than silently
# defanging the probe.
assert 80_000 < DEFAULT_ABS_CAP <= 400_000, (
    "the collision probe below only discriminates a correct case-glob arm "
    "from a broken one when the cap is strictly above SMALL_THRESHOLD and at "
    "or below 40% of the 1M window"
)

# Literal, not derived from the production ternary formula — mirroring that
# formula here would make the two threshold tests below tautological on
# formula shape. Four verified 200k models and four verified 1M models,
# chosen so the two 1M Opus rows flank the 200k Opus 4.5/4.1 entries — a
# same-major-version sanity check that 4.5/4.1 (200k) and 4.6/4.8 (1M) don't
# collapse onto the same window under a careless broad `case` arm (e.g.
# claude-opus-4-*).
KNOWN_MODEL_THRESHOLDS = [
    ("claude-haiku-4-5", SMALL_WINDOW, EFFECTIVE_SMALL_THRESHOLD),
    ("claude-sonnet-4-5", SMALL_WINDOW, EFFECTIVE_SMALL_THRESHOLD),
    ("claude-opus-4-5", SMALL_WINDOW, EFFECTIVE_SMALL_THRESHOLD),
    ("claude-opus-4-1", SMALL_WINDOW, EFFECTIVE_SMALL_THRESHOLD),
    ("claude-sonnet-5", LARGE_WINDOW, LARGE_THRESHOLD),
    ("claude-opus-5", LARGE_WINDOW, LARGE_THRESHOLD),
    ("claude-opus-4-6", LARGE_WINDOW, LARGE_THRESHOLD),
    ("claude-opus-4-8", LARGE_WINDOW, LARGE_THRESHOLD),
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


def _run_hook(
    payload: dict, tmp_path: Path, extra_env: dict | None = None
) -> subprocess.CompletedProcess:
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
    hook_event_name: str = "UserPromptSubmit",
) -> dict:
    return {
        "session_id": session_id,
        "transcript_path": str(transcript_path),
        "hook_event_name": hook_event_name,
    }


def _log_path(tmp_path: Path, config_dir: Path | None = None) -> Path:
    base = config_dir if config_dir is not None else tmp_path / ".claude"
    return base / ".handoff-nudge.log"


def _marker_path(
    tmp_path: Path, session_id: str = SESSION_ID, config_dir: Path | None = None
) -> Path:
    base = config_dir if config_dir is not None else tmp_path / ".claude"
    return base / ".handoff-nudge-fired.d" / session_id


def _drift_marker_path(
    tmp_path: Path, session_id: str = SESSION_ID, config_dir: Path | None = None
) -> Path:
    base = config_dir if config_dir is not None else tmp_path / ".claude"
    return base / ".handoff-nudge-fired.d" / f"{session_id}-drift"


# ---------------------------------------------------------------------------
# --check mode helpers
# ---------------------------------------------------------------------------

# --check takes no stdin payload: it resolves its own session by walking
# process ancestors for a sessions/<pid> entry. Under a bare subprocess.run
# with a list argv and no shell, the hook's $PPID is this pytest process, so
# seeding sessions/<os.getpid()> puts the entry at hop 1. Isolation comes from
# the tmp_path-scoped HOME, not from PID uniqueness — tests sharing the one
# real pytest PID never collide because their config dirs differ.


def _live_lstart(pid: int) -> str:
    """The process start time as the hook reads it.

    Mirrors capture-session-id.sh's pinned `TZ=UTC LC_ALL=C ps -o lstart=`
    recipe, including command-substitution's trailing-newline strip, so the
    stored and live values compare byte-for-byte the way the hook expects.
    """
    proc = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(pid)],
        capture_output=True,
        text=True,
        env={**os.environ, "TZ": "UTC", "LC_ALL": "C"},
        check=False,
    )
    return proc.stdout.rstrip("\n")


def _seed_session(
    config_dir: Path,
    pid: int,
    session_id: str = SESSION_ID,
    start_time: str | None = None,
) -> Path:
    """Write the sessions/<pid> lookup file capture-session-id.sh would write."""
    sessions_dir = config_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    entry = sessions_dir / str(pid)
    stamp = _live_lstart(pid) if start_time is None else start_time
    entry.write_text(f"{session_id}\n{stamp}\n")
    return entry


def _seed_transcript(
    config_dir: Path,
    records: list[dict],
    session_id: str = SESSION_ID,
    slug: str = "-tmp-project",
) -> Path:
    """Place a transcript where the session-id glob will find it."""
    project_dir = config_dir / "projects" / slug
    project_dir.mkdir(parents=True, exist_ok=True)
    transcript = project_dir / f"{session_id}.jsonl"
    _write_transcript(transcript, records)
    return transcript


def _check_env(tmp_path: Path, extra_env: dict | None = None) -> dict:
    env = {**os.environ, "HOME": str(tmp_path)}
    env.pop("CLAUDE_CONFIG_DIR", None)
    env.pop("HANDOFF_NUDGE_ABS_CAP", None)
    if extra_env:
        env.update(extra_env)
    return env


def _run_check(tmp_path: Path, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(NUDGE_HOOK), "--check"],
        capture_output=True,
        text=True,
        env=_check_env(tmp_path, extra_env),
        check=False,
    )


def _check_json(result: subprocess.CompletedProcess) -> dict:
    assert result.returncode == 0, f"--check must exit 0; stderr={result.stderr}"
    return json.loads(result.stdout)


def _default_config_dir(tmp_path: Path) -> Path:
    return tmp_path / ".claude"


# The hook caps its ancestor walk at CHECK_MAX_ANCESTOR_HOPS. Hop 1 is the
# hook's own $PPID, so N wrapper processes put the pytest-owned sessions entry
# at hop N+1 — the boundary tests below seed exactly at the cap and one past it.
CHECK_MAX_ANCESTOR_HOPS = 6


def _wrapper_chain(tmp_path: Path, depth: int, final_argv: list[str]) -> list[str]:
    """Build `depth` nested bash wrappers around final_argv.

    Returns the argv to invoke. Each wrapper runs the next as a forked child
    (a script file's last command is not exec-optimized), so the resulting
    process chain has exactly `depth` shells between the caller and final_argv.
    """
    argv = final_argv
    for level in range(depth):
        wrapper = tmp_path / f"hop{level}.sh"
        wrapper.write_text(
            "#!/bin/bash\n" + " ".join(shlex.quote(part) for part in argv) + "\n"
        )
        wrapper.chmod(0o755)
        argv = ["bash", str(wrapper)]
    return argv


def _fake_claude_chain(
    tmp_path: Path,
    final_argv: list[str],
    entry_text: str | None = None,
    below: bool = False,
) -> tuple[list[str], Path]:
    """Build an ancestor whose `ps -o comm=` basename is `claude`.

    A symlink to bash reports the symlink's own path, so the process running
    the script below is named `claude`; a shebang script named `claude` reports
    the interpreter on macOS and the script on Linux, which is why the symlink
    is what makes this portable. The script must not `exec` final_argv — the
    claude-named process has to survive as its ancestor.

    entry_text: None writes no sessions entry for the fake claude; "" writes an
    empty one; the sentinel "seed" writes the real two-line entry using
    capture-session-id.sh's own pinned `TZ=UTC LC_ALL=C ps -o lstart=` recipe
    (the PID cannot be known before the process exists, so this cannot be
    seeded from Python). below: run final_argv through one plain bash wrapper
    so the entry, when written, sits below the claude ancestor rather than on
    it.

    Returns the argv to invoke and the path the fake claude records its own
    `ps -o comm=` to, for the simulation precondition assertion.
    """
    assert entry_text in (None, "", "seed"), f"unknown entry_text: {entry_text!r}"
    comm_file = tmp_path / "fake-claude-comm"
    lines = ["#!/bin/bash", f'ps -o comm= -p $$ > {shlex.quote(str(comm_file))}']
    if entry_text is not None:
        sessions_dir = tmp_path / ".claude" / "sessions"
        target = f'{shlex.quote(str(sessions_dir))}/$$'
        if entry_text == "seed":
            lines.append(f'mkdir -p {shlex.quote(str(sessions_dir))}')
            lines.append(
                f"printf '%s\\n%s\\n' {shlex.quote(SESSION_ID)} "
                f'"$(TZ=UTC LC_ALL=C ps -o lstart= -p $$)" > {target}'
            )
        else:
            lines.append(f'mkdir -p {shlex.quote(str(sessions_dir))}')
            lines.append(f'printf "" > {target}')
    inner = _wrapper_chain(tmp_path, 1, final_argv) if below else final_argv
    lines.append(" ".join(shlex.quote(part) for part in inner))
    script = tmp_path / "fake-claude-body.sh"
    script.write_text("\n".join(lines) + "\n")
    script.chmod(0o755)
    fake_claude = tmp_path / "claude"
    fake_claude.symlink_to(shutil.which("bash"))
    return [str(fake_claude), str(script)], comm_file


def _assert_simulated_claude(comm_file: Path) -> None:
    """Fail loudly if the symlink trick did not produce a claude-named process.

    Without this the hook assertions below would still pass on a platform where
    `ps -o comm=` reports something else — testing nothing rather than failing.
    """
    assert comm_file.exists(), "the fake claude never ran"
    comm = comm_file.read_text().strip()
    basename = comm.lstrip("-").rpartition("/")[2]
    assert basename == "claude", f"simulation did not report as claude: {comm!r}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNudgeHandoffNearContextCap:
    def test_below_threshold_is_silent(self, tmp_path):
        """Token sum below 80 000: no stdout, no marker, no log (skip line was removed)."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_record(input_tok=30000, output_tok=20000)])
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert not _marker_path(tmp_path).exists()
        # Below threshold: hook exits silently with no log output.
        assert not _log_path(tmp_path).exists()

    @pytest.mark.parametrize("hook_event_name", HOOK_EVENT_NAMES)
    def test_above_threshold_fires_nudge(self, tmp_path, hook_event_name):
        """Token sum >= threshold, spread across all four usage fields, fires: JSON emitted, marker created, log has 'nudged'."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(
            transcript,
            [_assistant_record(cache_read=580000, cache_create=20000, input_tok=30000, output_tok=20000)],
        )
        result = _run_hook(_base_payload(transcript, hook_event_name=hook_event_name), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() != ""
        payload = json.loads(result.stdout)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        assert "handoff" in ctx.lower() or "/handoff" in ctx
        assert payload["hookSpecificOutput"]["hookEventName"] == hook_event_name
        assert _marker_path(tmp_path).exists()
        log_text = _log_path(tmp_path).read_text()
        assert "nudged" in log_text
        assert f"est={ABOVE_LARGE}" in log_text
        assert f"event={hook_event_name}" in log_text

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

    @pytest.mark.parametrize("hook_event_name", HOOK_EVENT_NAMES)
    def test_schema_drift_logs_and_exits(self, tmp_path, hook_event_name):
        """Usage block present with all token fields 0/null: log schema-drift, exit 0, no nudge marker."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_record()])  # all fields default to 0
        result = _run_hook(_base_payload(transcript, hook_event_name=hook_event_name), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert not _marker_path(tmp_path).exists()
        log = _log_path(tmp_path)
        assert log.exists()
        log_text = log.read_text()
        assert "schema-drift" in log_text
        assert f"event={hook_event_name}" in log_text
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

    def test_missing_hook_event_name_falls_back_to_user_prompt_submit(self, tmp_path):
        """A payload with no hook_event_name key at all defaults to 'UserPromptSubmit',
        matching the hook's pre-Stop-registration behavior every existing caller relied on."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(ABOVE_LARGE)])
        payload = {
            "session_id": SESSION_ID,
            "transcript_path": str(transcript),
        }
        result = _run_hook(payload, tmp_path)
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        log_text = _log_path(tmp_path).read_text()
        assert "event=UserPromptSubmit" in log_text

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

    @pytest.mark.parametrize("model,window,threshold", KNOWN_MODEL_THRESHOLDS)
    def test_fires_at_exactly_threshold_for_model(self, tmp_path, model, window, threshold):
        """Each known model ID fires when its own effective threshold is met exactly."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(threshold, model=model)])
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() != ""

    @pytest.mark.parametrize("model,window,threshold", KNOWN_MODEL_THRESHOLDS)
    def test_silent_one_below_threshold_for_model(self, tmp_path, model, window, threshold):
        """Each known model ID stays silent one token below its own effective threshold."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(threshold - 1, model=model)])
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_old_120k_constant_no_longer_fires_on_1m_models(self, tmp_path):
        """135 000 (fired under the old flat 120 000 constant) is now silent on a 1M model — well
        below the 360000 absolute cap. Direct GH-556 regression test."""
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

        Parameterized on SMALL_THRESHOLD, not the cap: a buggy arm that wrongly matched the 200k
        glob would fire at exactly SMALL_THRESHOLD, while the correct arm (falling to the 1M
        default) stays silent there for any cap above it — see the module-level range assertion
        above and the positive control below.
        """
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(SMALL_THRESHOLD, model=model_id)])
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == "", (
            f"{model_id} matched a 200k arm by string-prefix collision instead of falling to the 1M default"
        )

    @pytest.mark.parametrize("model_id", COLLIDING_MODEL_IDS)
    def test_longer_numeral_fires_at_1m_arm_effective_threshold(self, tmp_path, model_id):
        """Positive control for the probe above: the same colliding model ID DOES fire once its
        estimate reaches the 1M arm's effective threshold (the cap) — proving it correctly falls
        to that arm and respects its threshold, not merely that it fails to match the 200k one.
        """
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(LARGE_THRESHOLD, model=model_id)])
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() != "", (
            f"{model_id} should fall to the 1M arm and fire once its estimate reaches the cap"
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

    # -----------------------------------------------------------------------
    # The true no-match default arm — distinct from the collision probe above,
    # which pins a *mismatched-prefix* model ID. This one has no colliding
    # `case`-statement prefix at all, and its effective threshold now
    # reflects the cap rather than the "may never fire" assumption the old
    # flat-400,000 default carried.
    # -----------------------------------------------------------------------

    @pytest.mark.parametrize("total,expect_fire", [(LARGE_THRESHOLD - 1, False), (LARGE_THRESHOLD, True)])
    def test_true_no_match_default_arm_effective_threshold_reflects_cap(self, tmp_path, total, expect_fire):
        """A model ID with no `case`-statement prefix overlap at all still resolves to the 1M
        default, whose effective threshold is the absolute cap, not the raw 400,000 pct value."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(total, model="some-other-vendor-model")])
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert (result.stdout.strip() != "") == expect_fire

    # -----------------------------------------------------------------------
    # HANDOFF_NUDGE_ABS_CAP consumer override
    # -----------------------------------------------------------------------

    def test_abs_cap_override_changes_effective_threshold(self, tmp_path):
        """A valid HANDOFF_NUDGE_ABS_CAP overrides the default cap for 1M-window models."""
        custom_cap = 200_000
        below = tmp_path / "below.jsonl"
        _write_transcript(below, [_record_totalling(custom_cap - 1, model="claude-sonnet-5")])
        result_below = _run_hook(
            _base_payload(below, session_id="override-below"),
            tmp_path,
            extra_env={"HANDOFF_NUDGE_ABS_CAP": str(custom_cap)},
        )
        assert result_below.returncode == 0
        assert result_below.stdout.strip() == ""

        at_cap = tmp_path / "at.jsonl"
        _write_transcript(at_cap, [_record_totalling(custom_cap, model="claude-sonnet-5")])
        result_at = _run_hook(
            _base_payload(at_cap, session_id="override-at"),
            tmp_path,
            extra_env={"HANDOFF_NUDGE_ABS_CAP": str(custom_cap)},
        )
        assert result_at.returncode == 0
        assert result_at.stdout.strip() != ""

    def test_abs_cap_override_unset_falls_back_to_default(self, tmp_path):
        """With HANDOFF_NUDGE_ABS_CAP unset, the 1M-window arm uses the shipped default cap."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(LARGE_THRESHOLD, model="claude-sonnet-5")])
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() != ""

    @pytest.mark.parametrize(
        "malformed_value", ["abc", "080000", "", "-1", "1.5", "1e5", "9223372036854775808"]
    )
    def test_abs_cap_malformed_override_falls_back_to_default_not_zero(self, tmp_path, malformed_value):
        """A malformed HANDOFF_NUDGE_ABS_CAP (non-numeric, zero-padded, empty, negative,
        non-integer, or 10+ digits — which risks wrapping negative in bash's signed 64-bit
        arithmetic, e.g. 2**63) must fall back to the shipped default rather than degrade
        THRESHOLD toward 0/unset/negative — which would fire on every session, the opposite of
        "override ignored"."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(
            transcript, [_record_totalling(LARGE_THRESHOLD - 1, model="claude-sonnet-5")]
        )
        result = _run_hook(
            _base_payload(transcript), tmp_path,
            extra_env={"HANDOFF_NUDGE_ABS_CAP": malformed_value},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "", (
            f"malformed HANDOFF_NUDGE_ABS_CAP={malformed_value!r} should fall back to the "
            "default cap, not fire below it"
        )

    @pytest.mark.parametrize(
        "malformed_value", ["abc", "080000", "", "-1", "1.5", "1e5", "9223372036854775808"]
    )
    def test_abs_cap_malformed_override_positive_control_fires_at_default(self, tmp_path, malformed_value):
        """Positive control for the test above: proves the fallback actually lands on the
        shipped default (LARGE_THRESHOLD) rather than some other silent-non-firing state —
        e.g. the "080000" case reaches THRESHOLD via an invalid-octal arithmetic error rather
        than the case guard, which the negative-only test above cannot distinguish from a
        guard regression that leaves the hook permanently silent."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(
            transcript, [_record_totalling(LARGE_THRESHOLD, model="claude-sonnet-5")]
        )
        result = _run_hook(
            _base_payload(transcript), tmp_path,
            extra_env={"HANDOFF_NUDGE_ABS_CAP": malformed_value},
        )
        assert result.returncode == 0
        assert result.stdout.strip() != "", (
            f"malformed HANDOFF_NUDGE_ABS_CAP={malformed_value!r} should fall back to the "
            "default cap and fire at it, not stay silent indefinitely"
        )

    # -----------------------------------------------------------------------
    # M3: additionalContext states the computed threshold, ordering hazard
    # -----------------------------------------------------------------------

    @pytest.mark.parametrize("model,threshold", [("claude-haiku-4-5", SMALL_THRESHOLD), ("claude-sonnet-5", LARGE_THRESHOLD)])
    def test_additional_context_states_the_computed_threshold(self, tmp_path, model, threshold):
        """additionalContext embeds THRESHOLD via --argjson, on both a 200k and a 1M model —
        the only thing that would catch a CONTEXT_WINDOW-instead-of-THRESHOLD wiring slip."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(threshold, model=model)])
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        assert str(threshold) in ctx

    def test_jq_failure_building_context_does_not_burn_marker_or_log(self, tmp_path):
        """A jq failure while building additionalContext must not write the marker or log line —
        the capture-then-test fix must not silently swallow that failure the way the original
        `jq -n … 2>/dev/null || true` did. A shim `jq` on PATH fails only the final `-n` build
        call; earlier extraction calls (`-r`, `-s`) still delegate to the real jq."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(ABOVE_LARGE)])

        real_jq = shutil.which("jq")
        fake_bin = tmp_path / "fakebin"
        fake_bin.mkdir()
        fake_jq = fake_bin / "jq"
        fake_jq.write_text(
            "#!/bin/bash\n"
            "for arg in \"$@\"; do\n"
            "  if [ \"$arg\" = \"-n\" ]; then exit 1; fi\n"
            "done\n"
            f'exec "{real_jq}" "$@"\n'
        )
        fake_jq.chmod(0o755)

        result = _run_hook(
            _base_payload(transcript), tmp_path,
            extra_env={"PATH": f"{fake_bin}:{os.environ['PATH']}"},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert not _marker_path(tmp_path).exists()
        assert not _log_path(tmp_path).exists()

    # -----------------------------------------------------------------------
    # CLAUDE_CONFIG_DIR resolution
    # -----------------------------------------------------------------------

    def test_uses_config_dir_when_set(self, tmp_path):
        """CLAUDE_CONFIG_DIR relocates the marker and log locations: the
        nudge fires under CONFIG_DIR, never under $HOME/.claude."""
        config_dir = tmp_path / "profile"
        config_dir.mkdir()
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(ABOVE_LARGE)])
        result = _run_hook(
            _base_payload(transcript), tmp_path, extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)}
        )
        assert result.returncode == 0
        assert result.stdout.strip() != ""
        assert _marker_path(tmp_path, config_dir=config_dir).exists()
        assert not _marker_path(tmp_path).exists()
        log_text = _log_path(tmp_path, config_dir=config_dir).read_text()
        assert "nudged" in log_text
        assert not _log_path(tmp_path).exists()

    def test_killswitch_at_config_dir_suppresses(self, tmp_path):
        """The kill-switch is read from CONFIG_DIR when CLAUDE_CONFIG_DIR is
        set, not from $HOME/.claude."""
        config_dir = tmp_path / "profile"
        config_dir.mkdir()
        (config_dir / ".handoff-nudge-disabled").touch()
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(ABOVE_LARGE)])
        result = _run_hook(
            _base_payload(transcript), tmp_path, extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)}
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_relative_config_dir_fails_open(self, tmp_path):
        """A relative CLAUDE_CONFIG_DIR is unresolvable (_lib_config_dir
        returns 1); this informational hook fails open rather than resolve
        against an unstated cwd."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(ABOVE_LARGE)])
        result = _run_hook(
            _base_payload(transcript), tmp_path, extra_env={"CLAUDE_CONFIG_DIR": "relative/profile"}
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""


class TestCheckMode:
    """Read-only --check mode: reports the session's estimate, writes nothing.

    Consumed by plan-it Step 7 and the handoff skill, which branch on `status`
    and act on `over_threshold`. Refusing is a first-class outcome: every
    unresolved condition returns a named reason rather than a guessed number,
    because a confident number for the wrong session is worse than none.
    """

    def _seeded(self, tmp_path, total=LARGE_THRESHOLD - 1, model="claude-opus-5"):
        """Seed a resolvable session at hop 1 and return its config dir."""
        config_dir = _default_config_dir(tmp_path)
        config_dir.mkdir(parents=True, exist_ok=True)
        _seed_session(config_dir, os.getpid())
        _seed_transcript(config_dir, [_record_totalling(total, model=model)])
        return config_dir

    # -- side-effect freedom ------------------------------------------------

    def test_writes_no_marker_and_no_log(self, tmp_path):
        """--check must not consume the session's one nudge or append to the
        log that transcript-analysis.py reads as conversion evidence."""
        config_dir = self._seeded(tmp_path, total=ABOVE_LARGE)
        payload = _check_json(_run_check(tmp_path))
        assert payload["status"] == "ok"
        assert payload["over_threshold"] is True
        assert not _marker_path(tmp_path, config_dir=config_dir).exists()
        assert not _drift_marker_path(tmp_path, config_dir=config_dir).exists()
        assert not _log_path(tmp_path, config_dir=config_dir).exists()

    def test_does_not_read_stdin(self, tmp_path):
        """The dispatch must precede the hook's unconditional `cat`.

        Invoked from a Bash tool call with no redirect, that `cat` reads
        inherited stdin and blocks. pytest's own stdin is already closed, so a
        regressed ordering would still pass a plain subprocess.run — this holds
        the pipe open and never writes, so reading stdin hangs and fails here.
        """
        self._seeded(tmp_path)
        proc = subprocess.Popen(
            [str(NUDGE_HOOK), "--check"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=_check_env(tmp_path),
        )
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            pytest.fail("--check blocked on stdin; the dispatch runs after the `cat`")
        stdout = proc.stdout.read()
        proc.stdout.close()
        proc.stderr.close()
        proc.stdin.close()
        assert json.loads(stdout)["status"] == "ok"

    # -- ancestor walk ------------------------------------------------------

    def test_resolves_through_multi_hop_ancestor_walk(self, tmp_path):
        """The walk must advance past an ancestor with no sessions entry.

        Production needs 2-3 hops (a Bash tool call's shell, or a subshell);
        seeding at hop 1 alone would leave the loop itself untested. What
        discriminates is the pair of assertions at the end: the session
        resolved, and no entry existed at the wrapper the walk had to cross.
        Capping the loop at one hop turns this into
        `cannot-resolve`/`session-id-unresolved`.
        """
        config_dir = self._seeded(tmp_path)
        pid_file = tmp_path / "wrapper.pid"
        wrapper = tmp_path / "wrapper.sh"
        wrapper.write_text('#!/bin/bash\nprintf "%s" "$$" > "$2"\n"$1" --check\n')
        wrapper.chmod(0o755)
        result = subprocess.run(
            ["bash", str(wrapper), str(NUDGE_HOOK), str(pid_file)],
            capture_output=True,
            text=True,
            env=_check_env(tmp_path),
            check=False,
        )
        wrapper_pid = int(pid_file.read_text())
        # The entry lives at the pytest PID, the wrapper's parent — resolving
        # it means the walk crossed the wrapper, which has no entry.
        payload = _check_json(result)
        assert payload["status"] == "ok"
        assert payload["session_id"] == SESSION_ID
        assert not (config_dir / "sessions" / str(wrapper_pid)).exists()

    @pytest.mark.parametrize(
        "wrappers, expected_status",
        [(CHECK_MAX_ANCESTOR_HOPS - 1, "ok"), (CHECK_MAX_ANCESTOR_HOPS, "cannot-resolve")],
        ids=["at-cap", "one-past-cap"],
    )
    def test_ancestor_walk_stops_at_the_hop_cap(self, tmp_path, wrappers, expected_status):
        """The cap is a deliberate bound — a wedged process table must become a
        fast refusal, not a hang — so both sides of it are pinned. N wrappers
        put the entry at hop N+1, so N = cap-1 is the last resolvable depth and
        N = cap is one past it. An off-by-one in the loop bound moves one of
        these two without moving the other.

        The one-past-cap arm is also the sole owner of `session-id-unresolved`:
        every `bash` hop fails the walk's claude-name check, so exhausting the
        cap is the only way to reach the post-loop refusal.
        """
        self._seeded(tmp_path, total=ABOVE_LARGE)
        argv = _wrapper_chain(tmp_path, wrappers, [str(NUDGE_HOOK), "--check"])
        result = subprocess.run(
            argv, capture_output=True, text=True, env=_check_env(tmp_path), check=False
        )
        payload = _check_json(result)
        assert payload["status"] == expected_status
        if expected_status == "cannot-resolve":
            assert payload["reason"] == "session-id-unresolved"
            assert "estimate" not in payload

    # -- stopping at the claude ancestor ------------------------------------

    def test_claude_ancestor_with_its_own_entry_resolves(self, tmp_path):
        """A claude ancestor carrying its own entry still resolves.

        Not coverage of the stop rule: when the entry exists the walk's
        `[ -f "$entry" ]` break fires before the name check runs. This pins
        that the added check did not disturb the ordinary resolution path.
        """
        config_dir = _default_config_dir(tmp_path)
        config_dir.mkdir(parents=True, exist_ok=True)
        _seed_transcript(config_dir, [_record_totalling(LARGE_THRESHOLD - 1)])
        argv, comm_file = _fake_claude_chain(
            tmp_path, [str(NUDGE_HOOK), "--check"], entry_text="seed"
        )
        result = subprocess.run(
            argv, capture_output=True, text=True, env=_check_env(tmp_path), check=False
        )
        _assert_simulated_claude(comm_file)
        payload = _check_json(result)
        assert payload["status"] == "ok"
        assert payload["session_id"] == SESSION_ID
        # No entry was seeded at the pytest PID, so the resolution came from
        # the claude ancestor's own entry rather than from a grandparent.
        assert not (config_dir / "sessions" / str(os.getpid())).exists()

    def test_claude_ancestor_without_entry_refuses_instead_of_using_parents(
        self, tmp_path
    ):
        """The regression test: a nested session must not inherit its parent's.

        The fake claude has no entry; the pytest PID above it has a valid one.
        Before the walk stopped at claude this returned "ok" carrying that
        grandparent's session id and token estimate.
        """
        config_dir = self._seeded(tmp_path, total=ABOVE_LARGE)
        argv, comm_file = _fake_claude_chain(tmp_path, [str(NUDGE_HOOK), "--check"])
        result = subprocess.run(
            argv, capture_output=True, text=True, env=_check_env(tmp_path), check=False
        )
        _assert_simulated_claude(comm_file)
        payload = _check_json(result)
        assert payload["status"] == "cannot-resolve"
        assert payload["reason"] == "session-id-missing-at-claude"
        assert "estimate" not in payload
        assert payload.get("session_id") != SESSION_ID
        assert (config_dir / "sessions" / str(os.getpid())).exists()

    def test_entry_below_the_claude_ancestor_still_resolves(self, tmp_path):
        """Stopping at claude is inclusive: entries under it still resolve.

        This is the sole behavioural difference from refusing the moment the
        walk reaches claude, and is otherwise only asserted in prose.
        """
        config_dir = _default_config_dir(tmp_path)
        config_dir.mkdir(parents=True, exist_ok=True)
        _seed_transcript(config_dir, [_record_totalling(LARGE_THRESHOLD - 1)])
        argv, comm_file = _fake_claude_chain(
            tmp_path, [str(NUDGE_HOOK), "--check"], entry_text="seed", below=True
        )
        result = subprocess.run(
            argv, capture_output=True, text=True, env=_check_env(tmp_path), check=False
        )
        _assert_simulated_claude(comm_file)
        payload = _check_json(result)
        assert payload["status"] == "ok"
        assert payload["session_id"] == SESSION_ID

    def test_empty_entry_at_claude_ancestor_reports_unresolved(self, tmp_path):
        """An entry that exists but is empty is not the missing-entry case.

        The walk breaks on entry existence, not validity, so the name check
        never runs and the post-loop guard owns the refusal. Pins that at the
        one hop where the new code and the malformed-entry path can interact.
        """
        config_dir = _default_config_dir(tmp_path)
        config_dir.mkdir(parents=True, exist_ok=True)
        _seed_transcript(config_dir, [_record_totalling(ABOVE_LARGE)])
        argv, comm_file = _fake_claude_chain(
            tmp_path, [str(NUDGE_HOOK), "--check"], entry_text=""
        )
        result = subprocess.run(
            argv, capture_output=True, text=True, env=_check_env(tmp_path), check=False
        )
        _assert_simulated_claude(comm_file)
        payload = _check_json(result)
        assert payload["status"] == "cannot-resolve"
        assert payload["reason"] == "session-id-unresolved"

    def test_refuses_on_stale_pid(self, tmp_path):
        """A stored start time that disagrees with the live process means the
        PID was reused; binding to it would report another session's number."""
        config_dir = _default_config_dir(tmp_path)
        config_dir.mkdir(parents=True, exist_ok=True)
        _seed_session(config_dir, os.getpid(), start_time="Mon Jan  1 00:00:00 2001")
        _seed_transcript(config_dir, [_record_totalling(ABOVE_LARGE)])
        payload = _check_json(_run_check(tmp_path))
        assert payload["status"] == "cannot-resolve"
        assert payload["reason"] == "session-id-stale-pid"

    @pytest.mark.parametrize(
        "malformed", [TRAVERSAL_SESSION_ID, "../../etc", "sess/id", "sess*", "sess?[a]"]
    )
    def test_refuses_malformed_session_id(self, tmp_path, malformed):
        """The session id becomes a glob and path component, and unlike the
        fire path's harness-supplied value this one is read off disk.

        The refusal reason is the whole assertion. `--check` has no write path
        at all, so containment here is enforced by refusing rather than by
        guarding a write — a no-file-created assertion would pass even with
        the validation deleted, and is deliberately not made.
        """
        config_dir = _default_config_dir(tmp_path)
        config_dir.mkdir(parents=True, exist_ok=True)
        _seed_session(config_dir, os.getpid(), session_id=malformed)
        payload = _check_json(_run_check(tmp_path))
        assert payload["status"] == "cannot-resolve"
        assert payload["reason"] == "session-id-malformed"

    @pytest.mark.parametrize(
        "entry_text",
        ["", "\n", f"{SESSION_ID}\n", "\nMon Jan  1 00:00:00 2001\n"],
        ids=["empty-file", "blank-line", "session-id-only", "missing-session-id"],
    )
    def test_refuses_malformed_sessions_entry(self, tmp_path, entry_text):
        """`sessions/<pid>` is an on-disk file with no format version, so a
        truncated or empty entry must refuse rather than compare an empty
        stored start time against a live one and call that a stale PID."""
        config_dir = _default_config_dir(tmp_path)
        config_dir.mkdir(parents=True, exist_ok=True)
        sessions_dir = config_dir / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        (sessions_dir / str(os.getpid())).write_text(entry_text)
        _seed_transcript(config_dir, [_record_totalling(ABOVE_LARGE)])
        payload = _check_json(_run_check(tmp_path))
        assert payload["status"] == "cannot-resolve"
        assert payload["reason"] in {"session-id-unresolved", "session-id-stale-pid"}

    # -- transcript resolution ----------------------------------------------

    def test_refuses_when_transcript_not_found(self, tmp_path):
        """Zero glob matches must not read as one — bash's default expands an
        unmatched pattern to the literal pattern string."""
        config_dir = _default_config_dir(tmp_path)
        config_dir.mkdir(parents=True, exist_ok=True)
        _seed_session(config_dir, os.getpid())
        (config_dir / "projects" / "-tmp-project").mkdir(parents=True)
        payload = _check_json(_run_check(tmp_path))
        assert payload["status"] == "cannot-resolve"
        assert payload["reason"] == "transcript-not-found"

    def test_refuses_when_transcript_ambiguous(self, tmp_path):
        """Two project dirs holding the same session id: refuse rather than
        pick one, since guessing reports a number from the wrong tree."""
        config_dir = _default_config_dir(tmp_path)
        config_dir.mkdir(parents=True, exist_ok=True)
        _seed_session(config_dir, os.getpid())
        _seed_transcript(config_dir, [_record_totalling(ABOVE_LARGE)], slug="-tmp-main")
        _seed_transcript(config_dir, [_record_totalling(ABOVE_LARGE)], slug="-tmp-worktree")
        payload = _check_json(_run_check(tmp_path))
        assert payload["status"] == "cannot-resolve"
        assert payload["reason"] == "transcript-ambiguous"

    def test_resolves_under_inherited_noglob(self, tmp_path):
        """--check runs from an arbitrary Bash tool call, so the caller's shell
        options are inherited. Under `set -f` an unguarded pattern stays
        unexpanded and reads as one match holding the literal pattern, which
        would surface as usage-block-missing instead of the real estimate."""
        self._seeded(tmp_path, total=ABOVE_LARGE)
        wrapper = tmp_path / "noglob.sh"
        wrapper.write_text('#!/bin/bash\nset -f\n"$1" --check\nexit 0\n')
        wrapper.chmod(0o755)
        result = subprocess.run(
            ["bash", str(wrapper), str(NUDGE_HOOK)],
            capture_output=True,
            text=True,
            env=_check_env(tmp_path),
            check=False,
        )
        payload = _check_json(result)
        assert payload["status"] == "ok"
        assert payload["estimate"] == ABOVE_LARGE

    def test_refuses_when_usage_block_missing(self, tmp_path):
        """A transcript with no assistant usage record has nothing to sum."""
        config_dir = _default_config_dir(tmp_path)
        config_dir.mkdir(parents=True, exist_ok=True)
        _seed_session(config_dir, os.getpid())
        _seed_transcript(config_dir, [{"type": "user", "message": {"content": []}}])
        payload = _check_json(_run_check(tmp_path))
        assert payload["status"] == "cannot-resolve"
        assert payload["reason"] == "usage-block-missing"

    # -- config dir ---------------------------------------------------------

    def test_refuses_on_unresolvable_config_dir(self, tmp_path):
        """A relative CLAUDE_CONFIG_DIR resolves differently per invocation
        cwd; the fire path fails open, --check names the reason instead."""
        self._seeded(tmp_path)
        payload = _check_json(
            _run_check(tmp_path, extra_env={"CLAUDE_CONFIG_DIR": "relative/profile"})
        )
        assert payload["status"] == "cannot-resolve"
        assert payload["reason"] == "config-dir-unresolved"

    def test_resolves_from_overridden_config_dir(self, tmp_path):
        """sessions/, the transcript glob root, and the kill-switch all come
        from CLAUDE_CONFIG_DIR when set, not from $HOME/.claude."""
        config_dir = tmp_path / "profile"
        config_dir.mkdir()
        _seed_session(config_dir, os.getpid())
        _seed_transcript(config_dir, [_record_totalling(ABOVE_LARGE)])
        (config_dir / ".handoff-nudge-disabled").touch()
        # A decoy under the default location must not be what gets read.
        home_config = _default_config_dir(tmp_path)
        home_config.mkdir(parents=True, exist_ok=True)
        _seed_session(home_config, os.getpid(), session_id="decoy-session")
        payload = _check_json(
            _run_check(tmp_path, extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)})
        )
        assert payload["status"] == "ok"
        assert payload["session_id"] == SESSION_ID
        assert payload["nudge_disabled"] is True

    # -- reported fields ----------------------------------------------------

    def test_reports_below_threshold(self, tmp_path):
        self._seeded(tmp_path, total=LARGE_THRESHOLD - 1)
        payload = _check_json(_run_check(tmp_path))
        assert payload["status"] == "ok"
        assert payload["estimate"] == LARGE_THRESHOLD - 1
        assert payload["threshold"] == LARGE_THRESHOLD
        assert payload["over_threshold"] is False

    @pytest.mark.parametrize("total", [LARGE_THRESHOLD, ABOVE_LARGE])
    def test_reports_at_or_above_threshold(self, tmp_path, total):
        """The fire path fires at >= THRESHOLD, so --check must agree at the
        boundary or the two would disagree about the same session."""
        self._seeded(tmp_path, total=total)
        payload = _check_json(_run_check(tmp_path))
        assert payload["estimate"] == total
        assert payload["over_threshold"] is True

    @pytest.mark.parametrize("model, window, threshold", KNOWN_MODEL_THRESHOLDS)
    def test_known_model_reported_as_recognized(self, tmp_path, model, window, threshold):
        """Both verified lists are enumerated arms, so a listed 1M model must
        report recognized — otherwise the most common models read as
        defaulted and every consumer hedges on a correct number."""
        self._seeded(tmp_path, total=threshold, model=model)
        payload = _check_json(_run_check(tmp_path))
        assert payload["model"] == model
        assert payload["context_window"] == window
        assert payload["threshold"] == threshold
        assert payload["model_recognized"] is True

    @pytest.mark.parametrize("model", COLLIDING_MODEL_IDS + ["claude-unknown-9"])
    def test_unknown_model_reported_as_defaulted(self, tmp_path, model):
        """An ID with no arm takes the 1M default; saying so is the whole
        point of the field, since the threshold may not match that model."""
        self._seeded(tmp_path, total=ABOVE_LARGE, model=model)
        payload = _check_json(_run_check(tmp_path))
        assert payload["context_window"] == LARGE_WINDOW
        assert payload["model_recognized"] is False

    def test_reports_already_fired(self, tmp_path):
        """Replaces the 'it fires once' caveat the skill bodies used to carry."""
        config_dir = self._seeded(tmp_path, total=ABOVE_LARGE)
        marker = _marker_path(tmp_path, config_dir=config_dir)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
        payload = _check_json(_run_check(tmp_path))
        assert payload["status"] == "ok"
        assert payload["already_fired"] is True

    def test_killswitch_reported_not_honoured(self, tmp_path):
        """The kill-switch suppresses notifying, not measuring — a session
        that explicitly asks for a number still gets one."""
        config_dir = self._seeded(tmp_path, total=ABOVE_LARGE)
        (config_dir / ".handoff-nudge-disabled").touch()
        payload = _check_json(_run_check(tmp_path))
        assert payload["status"] == "ok"
        assert payload["estimate"] == ABOVE_LARGE
        assert payload["nudge_disabled"] is True

    def test_abs_cap_override_changes_reported_threshold(self, tmp_path):
        """`compute_threshold` is shared with the fire path, so an override
        must reach --check's reported threshold too — otherwise a future edit
        that inlines the cap here would report a number the fire path would
        never act on, and nothing would fail."""
        override = 120_000
        self._seeded(tmp_path, total=override + 1)
        payload = _check_json(
            _run_check(tmp_path, extra_env={"HANDOFF_NUDGE_ABS_CAP": str(override)})
        )
        assert payload["threshold"] == override
        assert payload["over_threshold"] is True

    def test_emits_every_field_the_skill_bodies_branch_on(self, tmp_path):
        """`plan-it` Step 7 and the `handoff` warrant check tell an agent to
        branch on these field names. Nothing else ties that prose to the hook,
        so a rename here would desync guidance an LLM acts on at inference
        time without failing any test. A UUID-shaped id is used because that
        is what the harness actually supplies."""
        config_dir = _default_config_dir(tmp_path)
        config_dir.mkdir(parents=True, exist_ok=True)
        uuid_session = "6f1c2d3e-4a5b-4c6d-8e9f-0a1b2c3d4e5f"
        _seed_session(config_dir, os.getpid(), session_id=uuid_session)
        _seed_transcript(
            config_dir, [_record_totalling(ABOVE_LARGE)], session_id=uuid_session
        )
        payload = _check_json(_run_check(tmp_path))
        assert payload["session_id"] == uuid_session
        for field in (
            "status",
            "estimate",
            "threshold",
            "over_threshold",
            "model",
            "context_window",
            "model_recognized",
            "already_fired",
            "nudge_disabled",
        ):
            assert field in payload, f"{field} is referenced by a SKILL.md branch"

    def test_schema_drift_reported_without_writes(self, tmp_path):
        """All-zero token fields mean the transcript schema moved; --check
        says so and still writes neither the drift marker nor a log line."""
        config_dir = _default_config_dir(tmp_path)
        config_dir.mkdir(parents=True, exist_ok=True)
        _seed_session(config_dir, os.getpid())
        _seed_transcript(config_dir, [_record_totalling(0)])
        payload = _check_json(_run_check(tmp_path))
        assert payload["status"] == "schema-drift"
        assert payload["session_id"] == SESSION_ID
        assert not _drift_marker_path(tmp_path, config_dir=config_dir).exists()
        assert not _log_path(tmp_path, config_dir=config_dir).exists()
