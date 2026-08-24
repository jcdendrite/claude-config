"""Tests for nudge-handoff-near-context-cap.sh.

The hook is a PostToolBatch and Stop hook that emits a
hookSpecificOutput.additionalContext JSON payload when the estimated carried
token count crosses the lesser of 40% of the resolved model's context window
or an absolute-token cap (HANDOFF_NUDGE_ABS_CAP, default 150000). 200k models
stay at 80000 (below the default cap, unaffected); 1M models — including
unrecognized/missing model IDs, which default to the 1M window — are capped
at 150000 rather than the raw 400000 (40% of 1M). The nudge re-arms at
escalating token bands past the first fire — a marker file holds the
triggering estimate and gates subsequent turns until the estimate advances
HANDOFF_NUDGE_REARM_SPACING (default 80000) past it. Past
HANDOFF_NUDGE_BLOCK_AFTER (default 1) ignored re-arms in one session, a
further re-arm hard-blocks (stderr + exit 2) instead of emitting the
advisory JSON -- but only on PostToolBatch; on Stop, exit 2 would force the
conversation to continue, so that registration falls through to the
advisory path instead.

All tests sandbox $HOME via monkeypatch so markers and logs land in tmp_path
rather than the real $HOME.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from helpers import HOOKS_DIR, TRAVERSAL_SESSION_ID, build_path_without

NUDGE_HOOK = HOOKS_DIR / "nudge-handoff-near-context-cap.sh"

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

SESSION_ID = "test-session-nudge-001"

# Both events the hook is registered under; used only where the assertions
# differ per event (the emitted hookEventName and event= log field) — gate
# logic upstream of the event read (kill-switch, subagent, plan-mode,
# already-fired) doesn't branch on it, so those tests stay unparametrized.
# UserPromptSubmit is no longer a registered event (dropped in favor of
# PostToolBatch) -- its own fallback-label behavior is covered separately by
# test_missing_hook_event_name_falls_back_to_user_prompt_submit and
# test_unrecognized_event_falls_back_to_user_prompt_submit_and_bootstraps.
HOOK_EVENT_NAMES = ["PostToolBatch", "Stop"]

# HANDOFF_NUDGE_BLOCK_AFTER's shipped default -- the escalation ladder hard-
# blocks once a session's ignored-re-arm count reaches this value.
DEFAULT_BLOCK_AFTER = 1

# Must exceed 2 -- some ladder tests deliberately drive ignored_count to 2
# (see test_escalation_counter_concurrent_rearms_no_lost_update); 5 is
# otherwise arbitrary above that floor.
REARM_MECHANICS_BLOCK_AFTER = "5"

# Mirrors the hook's own window table so no test hand-computes a threshold.
LARGE_WINDOW = 1_000_000
SMALL_WINDOW = 200_000
SMALL_THRESHOLD = 80_000  # 40% of 200k — the pct arm, unaffected by the cap (A7)
DEFAULT_ABS_CAP = 150_000  # HANDOFF_NUDGE_ABS_CAP's shipped default
DEFAULT_REARM_SPACING = 80_000  # HANDOFF_NUDGE_REARM_SPACING's shipped default
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

SMALL_TRANSCRIPT_LINES = 200  # equals the hook's own `tail -n 200` window
LARGE_TRANSCRIPT_LINES = 150_000  # ~29MB; big enough that a regression to a
# full-file read produces a ~6x runtime ratio against SMALL_TRANSCRIPT_LINES,
# well clear of the noise ceiling below (measured: real hook ratio ~0.8-1.5x,
# regressed hook ratio ~6.0x, at this fixture size). `_interleaved_median_seconds`
# gives every sample its own session_id so each measured call is a fresh
# bootstrap scan, matching this calibration's cost model even after the
# incremental-read cache (a8db41d) made repeat-session calls near-free.
TRANSCRIPT_SCALING_RATIO = 3.0
# Absolute slack so a near-zero baseline on a fast, quiet machine can't make
# the ratio arbitrarily sensitive to scheduler noise — same values as
# test_require_plan_review.py's MARKER_SCALING_RATIO/_SLACK_SECONDS, which
# already established this pattern for the same "cost shouldn't scale with N"
# property.
TRANSCRIPT_SCALING_SLACK_SECONDS = 1.0

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


def _append_to_transcript(path: Path, records: list[dict]) -> None:
    """Append records to an already-written transcript, matching how a real
    Claude Code transcript only ever grows. A multi-fire test simulating a
    session's next state must use this (not a second _write_transcript,
    which replaces the whole file) once any fire has happened: the
    incremental-read cache advances a byte offset past what it already read,
    and a same-or-larger-sized rewrite with *different* content at that
    offset is indistinguishable, from the offset alone, from real growth --
    the hook has no way to tell "this session grew" from "this file was
    replaced," so a test simulating growth must actually grow the file.
    """
    with path.open("a") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")


def _path_without_timeout_or_gtimeout(fake_bin: Path) -> str:
    """Build a PATH with only the binaries this hook's fire path invokes
    (`cat`/`jq` for the payload/output JSON, `dirname` to locate _lib.sh,
    `tail`/`wc`/`tr`/`head` for read_latest_usage_cached's incremental scan,
    `mkdir`/`find`/`touch` for the marker dir), omitting both timeout(1) and
    gtimeout(1). Skips (does not silently under-symlink) when a needed real
    binary is itself absent from the test machine."""
    for tool in ("cat", "dirname", "find", "head", "jq", "mkdir", "tail", "touch", "tr", "wc"):
        real = shutil.which(tool)
        if not real:
            pytest.skip(f"{tool} not found in PATH")
        (fake_bin / tool).symlink_to(real)
    return str(fake_bin)


def _check_mode_path_without_timeout_or_gtimeout(fake_bin: Path) -> str:
    """Build a PATH with only the binaries run_check_mode's --check path
    invokes (`dirname` to locate _lib.sh, `jq` for the reported JSON,
    `ps`/`head`/`sed`/`tr` for the ancestor walk, `env` for the pinned-locale
    live-start read, `tail` for read_latest_usage), omitting both timeout(1)
    and gtimeout(1). Skips when a needed real binary is itself absent from
    the test machine."""
    for tool in ("dirname", "env", "head", "jq", "ps", "sed", "tail", "tr"):
        real = shutil.which(tool)
        if not real:
            pytest.skip(f"{tool} not found in PATH")
        (fake_bin / tool).symlink_to(real)
    return str(fake_bin)


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
    hook_event_name: str = "PostToolBatch",
) -> dict:
    return {
        "session_id": session_id,
        "transcript_path": str(transcript_path),
        "hook_event_name": hook_event_name,
    }


def _interleaved_median_seconds(
    small_transcript: Path, large_transcript: Path, tmp_path: Path, runs: int = 3
) -> tuple[float, float]:
    """Median wall-clock seconds for `runs` interleaved invocations against
    each transcript (small, large, small, large, ...), returned as
    (small_median, large_median).

    Interleaved order prevents a single load burst from biasing one arm's
    entire sample window (this hook forks more subprocesses per call than
    sibling tests, so single-sample runtime is noisier).

    Every call gets its own fresh session_id — the incremental-read cache is
    keyed by session_id alone, not transcript path, so reusing one across
    calls would make all but the first a free cache-hit read instead of the
    bounded `tail -n 200` bootstrap scan this test means to measure.
    """
    small_samples: list[float] = []
    large_samples: list[float] = []
    for i in range(runs):
        for label, transcript, samples in (
            ("small", small_transcript, small_samples),
            ("large", large_transcript, large_samples),
        ):
            payload = _base_payload(transcript, session_id=f"{SESSION_ID}-{label}-{i}")
            start = time.perf_counter()
            result = _run_hook(payload, tmp_path)
            samples.append(time.perf_counter() - start)
            assert result.returncode == 0
    small_samples.sort()
    large_samples.sort()
    mid = runs // 2
    return small_samples[mid], large_samples[mid]


def _perf_counter_sequence(durations: list[float]):
    """Yields paired start/end values so consecutive `time.perf_counter()`
    calls measure exactly the given elapsed durations, for deterministically
    testing code that times itself via `perf_counter() - start`."""
    clock = 0.0
    for duration in durations:
        yield clock
        clock += duration
        yield clock


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


def _scan_state_path(
    tmp_path: Path, session_id: str = SESSION_ID, config_dir: Path | None = None
) -> Path:
    base = config_dir if config_dir is not None else tmp_path / ".claude"
    return base / ".handoff-nudge-fired.d" / f"{session_id}-scan"


def _ignored_marker_path(
    tmp_path: Path, session_id: str = SESSION_ID, config_dir: Path | None = None
) -> Path:
    base = config_dir if config_dir is not None else tmp_path / ".claude"
    return base / ".handoff-nudge-fired.d" / f"{session_id}-ignored"


def _plant_stale_marker(
    tmp_path: Path, name: str, *, days_old: int = 31, config_dir: Path | None = None
) -> Path:
    """Create a marker file under MARKER_DIR old enough (mtime) to qualify
    for the hook's own 30-day sweep (`find ... -mtime +30 -delete`)."""
    base = config_dir if config_dir is not None else tmp_path / ".claude"
    marker_dir = base / ".handoff-nudge-fired.d"
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker = marker_dir / name
    marker.write_text("stale\n")
    stale_time = time.time() - days_old * 86400
    os.utime(marker, (stale_time, stale_time))
    return marker


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

    def test_fires_nudge_when_neither_timeout_nor_gtimeout_present(self, tmp_path):
        """Fail-open regression: with neither binary present, _lib_capped_for
        runs the tail/jq fire-path calls uncapped (see _lib.sh) rather than
        silently degrading — the nudge must still fire above threshold under
        this PATH."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(
            transcript,
            [_assistant_record(cache_read=580000, cache_create=20000, input_tok=30000, output_tok=20000)],
        )
        fake_bin = tmp_path / "fakebin-no-timeout-no-gtimeout"
        fake_bin.mkdir()
        restricted_path = _path_without_timeout_or_gtimeout(fake_bin)
        result = _run_hook(
            _base_payload(transcript), tmp_path, extra_env={"PATH": restricted_path}
        )
        assert result.returncode == 0
        assert result.stdout.strip() != ""
        payload = json.loads(result.stdout)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        assert "handoff" in ctx.lower() or "/handoff" in ctx
        assert _marker_path(tmp_path).exists()

    # The two tests below each distinguish a real 2s cap firing from the cap
    # silently widening to _lib_capped's 5s default, at one specific call
    # site each: read_latest_usage's `tail` call and the fire path's final
    # `jq` call. run_check_mode's three `_lib_capped_for` jq calls
    # (check_refuse, schema-drift, status:"ok") are reachable only through
    # --check mode and still lack duration-distinguishing coverage.

    @pytest.mark.timing
    def test_read_latest_usage_tail_killed_by_2s_cap_not_5s_default(self, tmp_path):
        """A `tail` shim that takes ~3.5s to produce output would complete
        fine under _lib_capped's 5s default, but must be killed under the 2s
        cap read_latest_usage's `_lib_capped_for 2 tail ...` call actually
        uses -- distinguishing the two rather than passing either way, the
        way a shim slower than any plausible cap would."""
        real_tail = shutil.which("tail")
        if not real_tail:
            pytest.skip("tail not found in PATH")
        if not shutil.which("timeout") and not shutil.which("gtimeout"):
            pytest.skip("neither timeout(1) nor gtimeout(1) available — cap cannot fire at all")
        transcript = tmp_path / "t.jsonl"
        _write_transcript(
            transcript,
            [_assistant_record(cache_read=580000, cache_create=20000, input_tok=30000, output_tok=20000)],
        )
        fake_bin = tmp_path / "fakebin-slow-tail"
        fake_bin.mkdir()
        slow_tail = fake_bin / "tail"
        slow_tail.write_text(f"#!/bin/bash\nsleep 3.5\nexec {real_tail} \"$@\"\n")
        slow_tail.chmod(0o755)

        start = time.perf_counter()
        result = _run_hook(
            _base_payload(transcript), tmp_path, extra_env={"PATH": f"{fake_bin}:{os.environ['PATH']}"}
        )
        elapsed = time.perf_counter() - start

        assert result.returncode == 0
        assert result.stdout.strip() == "", (
            f"hook fired despite the slow tail call being expected to time out at the "
            f"2s cap (took {elapsed:.1f}s) -- the cap may have collapsed to the 5s "
            "_lib_capped default"
        )

    @pytest.mark.timing
    def test_fire_path_jq_killed_by_2s_cap_not_5s_default(self, tmp_path):
        """A `jq` shim that takes ~3.5s on the fire path's final call
        (`_lib_capped_for 2 jq -n ... hookSpecificOutput ...`) would
        complete fine under _lib_capped's 5s default, but must be killed
        under the 2s cap this call site actually uses -- distinguishing the
        two the same way as the tail test above. The shim only slows the
        invocation whose filter contains "hookSpecificOutput" so
        read_latest_usage's own jq calls still complete fast enough for the
        hook to reach the fire path at all."""
        real_jq = shutil.which("jq")
        if not real_jq:
            pytest.skip("jq not found in PATH")
        if not shutil.which("timeout") and not shutil.which("gtimeout"):
            pytest.skip("neither timeout(1) nor gtimeout(1) available — cap cannot fire at all")
        transcript = tmp_path / "t.jsonl"
        _write_transcript(
            transcript,
            [_assistant_record(cache_read=580000, cache_create=20000, input_tok=30000, output_tok=20000)],
        )
        fake_bin = tmp_path / "fakebin-slow-jq"
        fake_bin.mkdir()
        slow_jq = fake_bin / "jq"
        slow_jq.write_text(
            "#!/bin/bash\n"
            'case "$*" in\n'
            "  *hookSpecificOutput*) sleep 3.5 ;;\n"
            "esac\n"
            f'exec {real_jq} "$@"\n'
        )
        slow_jq.chmod(0o755)

        start = time.perf_counter()
        result = _run_hook(
            _base_payload(transcript), tmp_path, extra_env={"PATH": f"{fake_bin}:{os.environ['PATH']}"}
        )
        elapsed = time.perf_counter() - start

        assert result.returncode == 0
        assert result.stdout.strip() == "", (
            f"hook fired despite the slow fire-path jq call being expected to time out "
            f"at the 2s cap (took {elapsed:.1f}s) -- the cap may have collapsed to the "
            "5s _lib_capped default"
        )
        assert not _marker_path(tmp_path).exists()

    def test_already_fired_is_silent(self, tmp_path):
        """When the marker holds a LAST_FIRED_AT within REARM_SPACING of ESTIMATE, subsequent calls produce no stdout."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(ABOVE_LARGE)])
        marker_dir = tmp_path / ".claude" / ".handoff-nudge-fired.d"
        marker_dir.mkdir(parents=True)
        _marker_path(tmp_path).write_text(f"{ABOVE_LARGE}\n")
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_legacy_zero_byte_marker_now_fires(self, tmp_path):
        """A zero-byte marker -- the shape a pre-rearm-change hook wrote via a bare
        `touch` -- matches the case pattern's empty-string arm and is treated as no
        prior fire, so the hook fires rather than staying silently suppressed
        forever (the failure mode a stuck-suppressed nudge would be)."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(ABOVE_LARGE)])
        marker_dir = tmp_path / ".claude" / ".handoff-nudge-fired.d"
        marker_dir.mkdir(parents=True)
        _marker_path(tmp_path).touch()
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() != ""

    # -----------------------------------------------------------------------
    # Re-arming: escalating token bands past the first fire
    # -----------------------------------------------------------------------

    def test_second_fire_suppressed_before_rearm_spacing(self, tmp_path):
        """Two real hook invocations: the second, still within REARM_SPACING of the
        first fire's estimate, is silent -- driven through the hook itself rather
        than a hand-seeded marker so the write/read newline convention is exercised
        for real."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(LARGE_THRESHOLD, model="claude-sonnet-5")])
        first = _run_hook(_base_payload(transcript), tmp_path)
        assert first.stdout.strip() != ""

        _append_to_transcript(
            transcript,
            [_record_totalling(LARGE_THRESHOLD + DEFAULT_REARM_SPACING - 40_000, model="claude-sonnet-5")],
        )
        second = _run_hook(_base_payload(transcript), tmp_path)
        assert second.returncode == 0
        assert second.stdout.strip() == ""

    def test_second_fire_allowed_after_rearm_spacing(self, tmp_path):
        """Two real hook invocations: the second, past LAST_FIRED_AT + REARM_SPACING,
        fires again and overwrites the marker with the new triggering estimate --
        not left at the old value, not empty, not touched-to-zero-byte. Pins
        HANDOFF_NUDGE_BLOCK_AFTER above the default so this real re-arm (mechanics
        under test) stays advisory rather than exercising the escalation ladder."""
        transcript = tmp_path / "t.jsonl"
        extra_env = {"HANDOFF_NUDGE_BLOCK_AFTER": REARM_MECHANICS_BLOCK_AFTER}
        _write_transcript(transcript, [_record_totalling(LARGE_THRESHOLD, model="claude-sonnet-5")])
        first = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)
        assert first.stdout.strip() != ""

        second_estimate = LARGE_THRESHOLD + DEFAULT_REARM_SPACING + 40_000
        _append_to_transcript(transcript, [_record_totalling(second_estimate, model="claude-sonnet-5")])
        second = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)
        assert second.returncode == 0
        assert second.stdout.strip() != ""
        marker_content = _marker_path(tmp_path).read_text()
        assert marker_content == f"{second_estimate}\n"

    @pytest.mark.parametrize(
        "second_estimate,expect_fire",
        [
            (LARGE_THRESHOLD + DEFAULT_REARM_SPACING - 1, False),
            (LARGE_THRESHOLD + DEFAULT_REARM_SPACING, True),
        ],
    )
    def test_rearm_boundary_at_last_fired_plus_spacing(self, tmp_path, second_estimate, expect_fire):
        """N-1/N boundary pair at the rearm threshold, matching this file's existing
        adjacent-pair convention for every other threshold it tests. Pins
        HANDOFF_NUDGE_BLOCK_AFTER above the default so the expect_fire=True case's
        real re-arm (mechanics under test) stays advisory rather than exercising
        the escalation ladder."""
        transcript = tmp_path / "t.jsonl"
        extra_env = {"HANDOFF_NUDGE_BLOCK_AFTER": REARM_MECHANICS_BLOCK_AFTER}
        _write_transcript(transcript, [_record_totalling(LARGE_THRESHOLD, model="claude-sonnet-5")])
        first = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)
        assert first.stdout.strip() != ""

        _append_to_transcript(transcript, [_record_totalling(second_estimate, model="claude-sonnet-5")])
        second = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)
        assert second.returncode == 0
        if expect_fire:
            assert second.stdout.strip() != ""
        else:
            assert second.stdout.strip() == ""

    @pytest.mark.parametrize(
        "marker_content",
        ["abc\n", "0999\n", "123456789\n", "0\n"],
        ids=["non-numeric", "leading-zero", "nine-plus-digits", "literal-zero"],
    )
    def test_corrupt_marker_content_treated_as_no_prior_fire(self, tmp_path, marker_content):
        """A marker holding non-numeric, leading-zero, 9+-digit, or literal-zero content
        -- e.g. a stale write from a future format change -- falls back to "no prior
        fire" via the 4-arm guard and fires again once ESTIMATE is past THRESHOLD,
        rather than misparsing or staying silently suppressed forever. At the default
        REARM_SPACING this case doesn't discriminate a literal "0" being accepted as a
        real LAST_FIRED_AT from it being correctly blanked -- see the dedicated
        large-spacing test below for that."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(ABOVE_LARGE)])
        marker_dir = tmp_path / ".claude" / ".handoff-nudge-fired.d"
        marker_dir.mkdir(parents=True)
        _marker_path(tmp_path).write_text(marker_content)
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() != ""

    def test_literal_zero_marker_not_accepted_as_real_last_fired_at(self, tmp_path):
        """A marker of "0" is never legitimately written by the hook itself -- a real
        fire only ever happens at ESTIMATE >= THRESHOLD > 0 -- so it must be treated as
        corrupt, not as "last fired at token 0". Discriminates the bug this test guards
        (verified live against the pre-fix hook this session): accepting a literal "0"
        as LAST_FIRED_AT computes LAST_FIRED_AT + REARM_SPACING = REARM_SPACING, which
        silently suppresses a fire that should occur whenever a HANDOFF_NUDGE_REARM_SPACING
        override is larger than the current ESTIMATE -- the exact opposite of the hook's
        documented "fail toward firing" posture for a corrupt marker. The default
        REARM_SPACING (80000) can't expose this: ESTIMATE must already clear THRESHOLD
        (>= 150000) to reach this code path at all, so 0 + 80000 is never large enough to
        suppress it -- only an oversized override makes the two interpretations diverge."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(400_000, model="claude-sonnet-5")])
        marker_dir = tmp_path / ".claude" / ".handoff-nudge-fired.d"
        marker_dir.mkdir(parents=True)
        _marker_path(tmp_path).write_text("0\n")
        result = _run_hook(
            _base_payload(transcript), tmp_path,
            extra_env={"HANDOFF_NUDGE_REARM_SPACING": "1000000"},
        )
        assert result.returncode == 0
        assert result.stdout.strip() != "", (
            "a literal-zero marker must be treated as no-prior-fire, not as a real "
            "LAST_FIRED_AT=0 that a large REARM_SPACING override can suppress against"
        )

    def test_three_fire_sequence_rearms_twice(self, tmp_path):
        """Fire, suppress, re-fire past the second band, in one session -- exercises
        the marker overwrite happening twice. Pins HANDOFF_NUDGE_BLOCK_AFTER above
        the default so the third call's real re-arm (mechanics under test) stays
        advisory rather than exercising the escalation ladder."""
        transcript = tmp_path / "t.jsonl"
        extra_env = {"HANDOFF_NUDGE_BLOCK_AFTER": REARM_MECHANICS_BLOCK_AFTER}
        _write_transcript(transcript, [_record_totalling(LARGE_THRESHOLD, model="claude-sonnet-5")])
        first = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)
        assert first.stdout.strip() != ""
        assert _marker_path(tmp_path).read_text() == f"{LARGE_THRESHOLD}\n"

        suppressed_estimate = LARGE_THRESHOLD + DEFAULT_REARM_SPACING - 1
        _append_to_transcript(transcript, [_record_totalling(suppressed_estimate, model="claude-sonnet-5")])
        second = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)
        assert second.stdout.strip() == ""
        assert _marker_path(tmp_path).read_text() == f"{LARGE_THRESHOLD}\n"

        third_estimate = LARGE_THRESHOLD + DEFAULT_REARM_SPACING
        _append_to_transcript(transcript, [_record_totalling(third_estimate, model="claude-sonnet-5")])
        third = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)
        assert third.stdout.strip() != ""
        assert _marker_path(tmp_path).read_text() == f"{third_estimate}\n"

    # -----------------------------------------------------------------------
    # PostToolBatch migration: incremental-read cache
    # -----------------------------------------------------------------------

    def test_incremental_read_advances_offset_and_reuses_cached_estimate_when_no_new_usage(self, tmp_path):
        """A transcript grown (appended to, not rewritten) between two fires:
        the stored offset after the second call matches the transcript's byte
        length at that point, and ESTIMATE is unchanged when the appended
        delta carries no new usage block."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(ABOVE_LARGE)])
        first = _run_hook(_base_payload(transcript), tmp_path)
        assert first.stdout.strip() != ""

        _append_to_transcript(transcript, [{"type": "user", "message": {"content": "continue"}}])
        second = _run_hook(_base_payload(transcript), tmp_path)
        assert second.returncode == 0

        offset, estimate, _model = _scan_state_path(tmp_path).read_text().splitlines()
        assert int(offset) == transcript.stat().st_size
        assert int(estimate) == ABOVE_LARGE

    def test_incremental_read_stops_offset_before_incomplete_trailing_line(self, tmp_path):
        """A fire mid-write, where the transcript's newly appended bytes are
        an incomplete JSON line: the stored offset must stop before it, and
        the next fire -- once the line completes -- picks up the completed
        record whole, not duplicated or dropped. Pins HANDOFF_NUDGE_BLOCK_AFTER
        above the default so the third call's real re-arm (mechanics under
        test) stays advisory rather than exercising the escalation ladder."""
        transcript = tmp_path / "t.jsonl"
        extra_env = {"HANDOFF_NUDGE_BLOCK_AFTER": REARM_MECHANICS_BLOCK_AFTER}
        _write_transcript(transcript, [_record_totalling(LARGE_THRESHOLD, model="claude-sonnet-5")])
        first = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)
        assert first.stdout.strip() != ""
        offset_after_first = transcript.stat().st_size

        second_estimate = LARGE_THRESHOLD + DEFAULT_REARM_SPACING + 40_000
        full_line = json.dumps(_record_totalling(second_estimate, model="claude-sonnet-5"))
        split_at = len(full_line) // 2
        with transcript.open("a") as f:
            f.write(full_line[:split_at])  # no trailing newline: caught mid-write

        second = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)
        assert second.returncode == 0
        offset, cached_estimate, _model = _scan_state_path(tmp_path).read_text().splitlines()
        assert int(offset) == offset_after_first, "offset must not advance past the incomplete trailing line"
        assert int(cached_estimate) == LARGE_THRESHOLD, "ESTIMATE must stay at the first fire's cached value"

        with transcript.open("a") as f:
            f.write(full_line[split_at:] + "\n")  # complete the line

        third = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)
        assert third.returncode == 0
        assert third.stdout.strip() != "", "the completed record must fire once whole, not stay dropped"
        offset2, estimate2, _model2 = _scan_state_path(tmp_path).read_text().splitlines()
        assert int(estimate2) == second_estimate
        assert int(offset2) == transcript.stat().st_size

    def test_incremental_read_falls_back_to_bootstrap_when_stored_offset_exceeds_file_size(self, tmp_path):
        """A stored -scan offset larger than the transcript's current size
        (simulating rotation/truncation) resets to a fresh bootstrap scan
        rather than erroring or silently trusting a stale cached estimate."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(ABOVE_LARGE)])
        scan_state = _scan_state_path(tmp_path)
        scan_state.parent.mkdir(parents=True, exist_ok=True)
        huge_offset = transcript.stat().st_size + 1_000_000
        scan_state.write_text(f"{huge_offset}\n999\nclaude-sonnet-5\n")

        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() != "", (
            "must fall back to a fresh bootstrap scan of the real transcript, not stay "
            "silent trusting the stale cached estimate of 999"
        )
        offset, estimate, _model = scan_state.read_text().splitlines()
        assert int(offset) == transcript.stat().st_size
        assert int(estimate) == ABOVE_LARGE

    def test_incremental_read_bootstraps_and_writes_scan_state_on_first_fire(self, tmp_path):
        """No -scan file yet: the bootstrap path runs (today's bounded scan)
        and writes a correctly-shaped 3-line state file (offset, estimate,
        model) for the next fire to read incrementally."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(ABOVE_LARGE, model="claude-sonnet-5")])
        scan_state = _scan_state_path(tmp_path)
        assert not scan_state.exists()

        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        assert scan_state.exists()
        lines = scan_state.read_text().splitlines()
        assert len(lines) == 3
        offset, estimate, model = lines
        assert int(offset) == transcript.stat().st_size
        assert int(estimate) == ABOVE_LARGE
        assert model == "claude-sonnet-5"

    def test_unrecognized_event_falls_back_to_user_prompt_submit_and_bootstraps(self, tmp_path):
        """A HOOK_EVENT value that is neither Stop nor PostToolBatch --
        simulating the exact landing-order failure mode this file's header
        names (settings.json registering PostToolBatch before this case arm
        supports it) -- falls back to the UserPromptSubmit label and still
        takes the bootstrap scan path, pinning the described bug class as a
        test rather than only avoiding it via commit discipline."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(ABOVE_LARGE)])
        result = _run_hook(
            _base_payload(transcript, hook_event_name="SomeFutureHookEvent"), tmp_path
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        log_text = _log_path(tmp_path).read_text()
        assert "event=UserPromptSubmit" in log_text
        assert _scan_state_path(tmp_path).exists()

    def test_unconditional_sweep_runs_even_when_the_call_does_not_fire(self, tmp_path):
        """The 30-day sweep now runs unconditionally near the top of the fire
        path (moved out of the emit-on-fire block so it also bounds the new
        -scan file's steady-state growth) -- a call that doesn't cross
        threshold (no nudge emitted) still evicts every stale marker file,
        across all four marker-file types, not just FIRED_MARKER/DRIFT_MARKER."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_assistant_record(input_tok=30000, output_tok=20000)])  # below threshold
        other_session = "other-stale-session-001"
        stale_fired = _plant_stale_marker(tmp_path, other_session)
        stale_drift = _plant_stale_marker(tmp_path, f"{other_session}-drift")
        stale_scan = _plant_stale_marker(tmp_path, f"{other_session}-scan")
        stale_ignored = _plant_stale_marker(tmp_path, f"{other_session}-ignored")

        result = _run_hook(_base_payload(transcript), tmp_path)

        assert result.returncode == 0
        assert result.stdout.strip() == ""
        for stale in (stale_fired, stale_drift, stale_scan, stale_ignored):
            assert not stale.exists(), f"{stale.name} should have been swept by the unconditional 30-day sweep"

    def test_jq_absent_fails_open_not_hard_blocked(self, tmp_path):
        """batch-gate's escalation ladder can intentionally exit 2, but that
        must only ever come from a real fire history -- with jq entirely
        absent (and no prior cached state), the hook must still fail open
        (exit 0, silent), never hard-block. GATE_HOOKS' auto-parametrized
        jq-absent behavior tests (test_hook_alignment.py) don't cover this
        hook -- its class is batch-gate, not gate -- so this is hand-written."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(ABOVE_LARGE)])
        farm_dir = tmp_path / "path-without-jq"
        farm_dir.mkdir()
        restricted_path = build_path_without("jq", farm_dir)
        result = _run_hook(
            _base_payload(transcript), tmp_path, extra_env={"PATH": restricted_path}
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert result.stderr.strip() == ""

    def test_wc_absent_fails_open_not_hard_blocked(self, tmp_path):
        """The incremental-read offset math depends on `wc`; its absence must
        also fail open like every other missing-dependency case in this
        file, never hard-block."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(ABOVE_LARGE)])
        farm_dir = tmp_path / "path-without-wc"
        farm_dir.mkdir()
        restricted_path = build_path_without("wc", farm_dir)
        result = _run_hook(
            _base_payload(transcript), tmp_path, extra_env={"PATH": restricted_path}
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert result.stderr.strip() == ""

    def test_tail_absent_fails_open_not_hard_blocked(self, tmp_path):
        """`tail` backs both read_latest_usage and
        _advance_offset_past_complete_lines; its absence must fail open like
        every other missing-dependency case in this file, never hard-block."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(ABOVE_LARGE)])
        farm_dir = tmp_path / "path-without-tail"
        farm_dir.mkdir()
        restricted_path = build_path_without("tail", farm_dir)
        result = _run_hook(
            _base_payload(transcript), tmp_path, extra_env={"PATH": restricted_path}
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert result.stderr.strip() == ""

    def test_find_absent_still_fires_and_never_hard_blocks(self, tmp_path):
        """`find` only backs the marker-directory sweep, not the read/decide
        path -- unlike jq/wc/tail, its absence must not suppress a fire that
        would otherwise happen. The sweep silently no-ops (`|| true`); the
        hook still emits its advisory JSON, and never hard-blocks."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(ABOVE_LARGE)])
        farm_dir = tmp_path / "path-without-find"
        farm_dir.mkdir()
        restricted_path = build_path_without("find", farm_dir)
        result = _run_hook(
            _base_payload(transcript), tmp_path, extra_env={"PATH": restricted_path}
        )
        assert result.returncode == 0
        assert result.stdout.strip() != ""

    def test_mkdir_absent_still_fires_and_never_hard_blocks(self, tmp_path):
        """`mkdir` backs the marker-directory sweep and
        read_latest_usage_cached's state-directory creation, not the
        read/decide path -- its absence must not suppress a fire that would
        otherwise happen, and must never hard-block. Marker/state-file
        writes into a directory that failed to be created silently no-op
        (`|| true` / redirection failure), but OUTPUT is built and printed
        regardless of whether those side-effect writes succeeded."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(ABOVE_LARGE)])
        farm_dir = tmp_path / "path-without-mkdir"
        farm_dir.mkdir()
        restricted_path = build_path_without("mkdir", farm_dir)
        result = _run_hook(
            _base_payload(transcript), tmp_path, extra_env={"PATH": restricted_path}
        )
        assert result.returncode == 0
        assert result.stdout.strip() != ""

    # -----------------------------------------------------------------------
    # PostToolBatch migration: gate correctness under the new event
    # -----------------------------------------------------------------------

    def test_subagent_gate_under_post_tool_batch(self, tmp_path):
        """The subagent gate still applies correctly under a
        PostToolBatch-shaped payload -- verified live this session that
        agent_type is present/absent exactly as it was under Stop/
        UserPromptSubmit, so no fail-safe redesign was needed here."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(ABOVE_LARGE)])
        payload = _base_payload(transcript, hook_event_name="PostToolBatch")
        payload["agent_type"] = "code-writer"
        result = _run_hook(payload, tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_plan_mode_gate_under_post_tool_batch(self, tmp_path):
        """The plan-mode gate still applies correctly under a
        PostToolBatch-shaped payload."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(ABOVE_LARGE)])
        payload = _base_payload(transcript, hook_event_name="PostToolBatch")
        payload["permission_mode"] = "plan"
        result = _run_hook(payload, tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    # -----------------------------------------------------------------------
    # Escalation ladder: advisory -> hard block after ignored re-arms
    # -----------------------------------------------------------------------

    @pytest.mark.timing
    def test_atomic_append_no_lost_writes_under_concurrency(self, tmp_path):
        """Exercises the exact `printf '.' >> file` O_APPEND idiom the
        escalation counter uses (see the hard-block's own source comment),
        directly and repeatedly, decoupled from the hook's own gate-timing
        logic. N processes append one byte each, spawned via subprocess.Popen
        (non-blocking, so process-creation overhead alone produces genuine
        overlapping execution windows -- no artificial synchronization
        needed). POSIX O_APPEND write atomicity guarantees the final file
        size is exactly N, with no write silently dropped -- the property
        test_escalation_counter_concurrent_rearms_no_lost_update below
        cannot itself prove, since a benign serialized execution and a real
        lost update produce the same observable outcome at that layer."""
        target = tmp_path / "concurrent-append-target"
        target.touch()
        n = 20
        procs = [
            subprocess.Popen(["sh", "-c", f"printf '.' >> {shlex.quote(str(target))}"]) for _ in range(n)
        ]
        for p in procs:
            assert p.wait() == 0
        assert target.stat().st_size == n

    def test_escalation_counter_concurrent_rearms_no_lost_update(self, tmp_path):
        """Two near-simultaneous PostToolBatch fires against the same
        already-armed session: a smoke check that concurrent invocation
        neither crashes nor over-counts past what any legitimate ordering
        could produce. This test alone cannot prove the append is atomic --
        see test_atomic_append_no_lost_writes_under_concurrency above for
        that -- because a fully benign serialized ordering (thread A
        completes, including its own FIRED_MARKER rewrite, before B starts)
        produces the identical observable result (ignored_count == 1, both
        exit 0) as a real lost update would: B's own ordinary rearm-spacing
        gate suppresses it before it ever reaches the increment, and there
        is no synchronization barrier here forcing genuine overlap instead.
        Marked timing (run serially, -m timing -n0) since heavier xdist
        parallel load skews which ordering is likelier, not because either
        ordering is itself invalid. Pins HANDOFF_NUDGE_BLOCK_AFTER above the
        default -- and above the "2" a genuine concurrent overlap can legitimately
        reach here -- so this test discriminates atomic-append correctness, not
        the escalation ladder."""
        transcript = tmp_path / "t.jsonl"
        extra_env = {"HANDOFF_NUDGE_BLOCK_AFTER": REARM_MECHANICS_BLOCK_AFTER}
        first_estimate = LARGE_THRESHOLD
        _write_transcript(transcript, [_record_totalling(first_estimate, model="claude-sonnet-5")])
        first = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)
        assert first.stdout.strip() != ""  # first fire: no increment (not a re-arm)

        rearm_estimate = first_estimate + DEFAULT_REARM_SPACING + 40_000
        _append_to_transcript(transcript, [_record_totalling(rearm_estimate, model="claude-sonnet-5")])

        exit_codes: list[int | None] = [None, None]

        def _run(i: int) -> None:
            exit_codes[i] = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env).returncode

        threads = [threading.Thread(target=_run, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Both stay advisory under the pinned HANDOFF_NUDGE_BLOCK_AFTER above,
        # regardless of ordering. ignored_count is 1 under full serialization
        # (see docstring) or 2 under genuine overlap -- both are legitimate;
        # anything outside {1, 2} would indicate corruption.
        assert all(code == 0 for code in exit_codes)
        ignored_count = _ignored_marker_path(tmp_path).stat().st_size
        assert ignored_count in (1, 2), f"expected 1 or 2 ignored re-arms, got {ignored_count}"

    def test_escalation_ladder_blocks_once_block_after_ignored_rearms_reached(self, tmp_path):
        """Advisory nudges keep firing (stdout JSON, exit 0) until
        HANDOFF_NUDGE_BLOCK_AFTER ignored re-arms are reached, at which
        point the hook hard-blocks (stderr, exit 2) instead. Also checks the
        log: only the hard-block fire's line carries action=block, not
        either advisory fire's."""
        transcript = tmp_path / "t.jsonl"
        extra_env = {"HANDOFF_NUDGE_BLOCK_AFTER": "2"}
        estimate = LARGE_THRESHOLD
        _write_transcript(transcript, [_record_totalling(estimate, model="claude-sonnet-5")])
        first = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)
        assert first.returncode == 0
        assert first.stdout.strip() != ""  # first fire: advisory, no increment

        estimate += DEFAULT_REARM_SPACING
        _append_to_transcript(transcript, [_record_totalling(estimate, model="claude-sonnet-5")])
        second = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)
        assert second.returncode == 0
        assert second.stdout.strip() != ""  # first re-arm (ignored count -> 1): still advisory

        estimate += DEFAULT_REARM_SPACING
        _append_to_transcript(transcript, [_record_totalling(estimate, model="claude-sonnet-5")])
        third = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)
        assert third.returncode == 2, "second re-arm (ignored count -> 2) reaches HANDOFF_NUDGE_BLOCK_AFTER=2"
        assert third.stdout.strip() == ""
        assert third.stderr.strip() != ""
        assert "/handoff" in third.stderr
        assert "HANDOFF_NUDGE_BLOCK_AFTER=" in third.stderr
        assert "genuinely almost done" not in third.stderr
        assert "too aggressive for your workflow" not in third.stderr
        assert _ignored_marker_path(tmp_path).stat().st_size == 2
        assert _marker_path(tmp_path).read_text() == f"{estimate}\n"

        nudged_lines = [line for line in _log_path(tmp_path).read_text().splitlines() if line.startswith("nudged")]
        assert len(nudged_lines) == 3
        assert "action=block" not in nudged_lines[0]
        assert "action=block" not in nudged_lines[1]
        assert nudged_lines[2].endswith("action=block")

    def test_escalation_ladder_resets_when_ignored_marker_removed(self, tmp_path):
        """Removing the -ignored marker (e.g. the handoff skill's conversion
        step, per SKILL.md) resets the escalation ladder for that session --
        the next re-arm is advisory again, not an immediate repeat block.
        Uses HANDOFF_NUDGE_BLOCK_AFTER=2 rather than 1: at 1, any single
        re-arm blocks regardless of whether the reset took effect, so the
        two outcomes would be indistinguishable."""
        transcript = tmp_path / "t.jsonl"
        extra_env = {"HANDOFF_NUDGE_BLOCK_AFTER": "2"}
        estimate = LARGE_THRESHOLD
        _write_transcript(transcript, [_record_totalling(estimate, model="claude-sonnet-5")])
        first = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)
        assert first.stdout.strip() != ""

        estimate += DEFAULT_REARM_SPACING
        _append_to_transcript(transcript, [_record_totalling(estimate, model="claude-sonnet-5")])
        second = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)
        assert second.returncode == 0
        assert second.stdout.strip() != "", "first re-arm (ignored count -> 1): still advisory"

        estimate += DEFAULT_REARM_SPACING
        _append_to_transcript(transcript, [_record_totalling(estimate, model="claude-sonnet-5")])
        third = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)
        assert third.returncode == 2, "second re-arm (ignored count -> 2) reaches HANDOFF_NUDGE_BLOCK_AFTER=2"

        ignored_marker = _ignored_marker_path(tmp_path)
        assert ignored_marker.exists()
        ignored_marker.unlink()

        estimate += DEFAULT_REARM_SPACING
        _append_to_transcript(transcript, [_record_totalling(estimate, model="claude-sonnet-5")])
        fourth = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)
        assert fourth.returncode == 0
        assert fourth.stdout.strip() != "", (
            "advisory again after the ignored-count marker was removed -- without the "
            "reset this re-arm would be the third ignored one, still >= HANDOFF_NUDGE_BLOCK_AFTER=2"
        )

    @pytest.mark.parametrize("hook_event_name", HOOK_EVENT_NAMES)
    def test_hard_block_only_fires_on_post_tool_batch(self, tmp_path, hook_event_name):
        """Regression test for the exit-code inversion: PostToolBatch's own
        exit-2 contract stops the agentic loop, but the same hook is also
        registered on Stop, where exit 2 instead forces the conversation to
        continue -- the opposite of a block. Under the identical ignored-
        count/block-after condition, PostToolBatch hard-blocks while Stop
        falls through to the advisory JSON-envelope path instead."""
        transcript = tmp_path / "t.jsonl"
        extra_env = {"HANDOFF_NUDGE_BLOCK_AFTER": "1"}
        estimate = LARGE_THRESHOLD
        _write_transcript(transcript, [_record_totalling(estimate, model="claude-sonnet-5")])
        first = _run_hook(
            _base_payload(transcript, hook_event_name=hook_event_name), tmp_path, extra_env=extra_env
        )
        assert first.returncode == 0
        assert first.stdout.strip() != ""  # first fire: advisory, no increment

        estimate += DEFAULT_REARM_SPACING
        _append_to_transcript(transcript, [_record_totalling(estimate, model="claude-sonnet-5")])
        second = _run_hook(
            _base_payload(transcript, hook_event_name=hook_event_name), tmp_path, extra_env=extra_env
        )
        if hook_event_name == "PostToolBatch":
            assert second.returncode == 2, "ignored count -> 1 reaches HANDOFF_NUDGE_BLOCK_AFTER=1 on PostToolBatch"
            assert second.stdout.strip() == ""
            assert second.stderr.strip() != ""
        else:
            assert second.returncode == 0, "Stop's exit 2 would force continuation, so it must not hard-block"
            assert second.stdout.strip() != ""
            payload = json.loads(second.stdout)
            assert payload["hookSpecificOutput"]["hookEventName"] == hook_event_name
            nudged_lines = [
                line for line in _log_path(tmp_path).read_text().splitlines() if line.startswith("nudged")
            ]
            assert "action=block" not in nudged_lines[-1]

    @pytest.mark.parametrize(
        "malformed_value", ["abc", "080000", "", "0", "-1", "1.5", "1e5", "9223372036854775808"]
    )
    def test_block_after_malformed_override_falls_back_to_default_not_zero(self, tmp_path, malformed_value):
        """A malformed override must fall back to the shipped default (1), not
        degrade toward 0 -- checked on this session's first-ever crossing, where a
        degraded BLOCK_AFTER=0 would hard-block immediately (0 >= 0) but a correct
        fallback stays advisory (0 >= 1 is false)."""
        transcript = tmp_path / "t.jsonl"
        estimate = LARGE_THRESHOLD
        _write_transcript(transcript, [_record_totalling(estimate, model="claude-sonnet-5")])
        extra_env = {"HANDOFF_NUDGE_BLOCK_AFTER": malformed_value}
        first = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)
        assert first.returncode == 0
        assert first.stdout.strip() != ""

    @pytest.mark.parametrize(
        "malformed_value", ["abc", "080000", "", "0", "-1", "1.5", "1e5", "9223372036854775808"]
    )
    def test_block_after_malformed_override_positive_control_blocks_at_default(self, tmp_path, malformed_value):
        """At the shipped default (1), range(DEFAULT_BLOCK_AFTER - 1) is range(0),
        so this test's own contribution is the post-loop call, which drives the
        real re-arm and asserts the default actually hard-blocks there (the sibling
        test only checks the fallback isn't degraded to 0)."""
        transcript = tmp_path / "t.jsonl"
        estimate = LARGE_THRESHOLD
        _write_transcript(transcript, [_record_totalling(estimate, model="claude-sonnet-5")])
        extra_env = {"HANDOFF_NUDGE_BLOCK_AFTER": malformed_value}
        result = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)
        assert result.returncode == 0
        assert result.stdout.strip() != ""

        for _ in range(DEFAULT_BLOCK_AFTER - 1):
            estimate += DEFAULT_REARM_SPACING
            _append_to_transcript(transcript, [_record_totalling(estimate, model="claude-sonnet-5")])
            result = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)
            assert result.returncode == 0
            assert result.stdout.strip() != ""

        estimate += DEFAULT_REARM_SPACING
        _append_to_transcript(transcript, [_record_totalling(estimate, model="claude-sonnet-5")])
        result = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)
        assert result.returncode == 2, (
            f"malformed HANDOFF_NUDGE_BLOCK_AFTER={malformed_value!r} should fall back to the "
            "default and block once that many re-arms are reached"
        )

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

    @pytest.mark.timing
    def test_latency_does_not_scale_with_transcript_size(self, tmp_path):
        """Hook runtime stays flat as the transcript grows, verified via a
        ratio rather than an absolute bound, since wall-clock time on this
        hook varies orders of magnitude with machine load: a regression to a
        full-file read inflates the large/small runtime ratio to ~6x, while
        correct O(1) behavior stays near 1x.
        """
        single_line = json.dumps(_assistant_record(input_tok=5000, output_tok=1000))
        small_transcript = tmp_path / "small.jsonl"
        small_transcript.write_text(
            "\n".join([single_line] * SMALL_TRANSCRIPT_LINES) + "\n"
        )
        large_transcript = tmp_path / "large.jsonl"
        large_transcript.write_text(
            "\n".join([single_line] * LARGE_TRANSCRIPT_LINES) + "\n"
        )

        small_seconds, large_seconds = _interleaved_median_seconds(
            small_transcript, large_transcript, tmp_path
        )

        allowed = (
            small_seconds * TRANSCRIPT_SCALING_RATIO
            + TRANSCRIPT_SCALING_SLACK_SECONDS
        )
        assert large_seconds < allowed, (
            f"{LARGE_TRANSCRIPT_LINES}-line transcript took {large_seconds:.3f}s vs "
            f"{small_seconds:.3f}s for {SMALL_TRANSCRIPT_LINES} lines -- over the "
            f"allowed {allowed:.3f}s. Runtime is scaling with transcript size, which "
            "suggests a regression to a full-file read (tail -n 200 should keep it flat)."
        )

    def test_interleaved_median_seconds_alternates_calls_and_picks_true_median(
        self, monkeypatch, tmp_path
    ):
        """Deterministic pin for `_interleaved_median_seconds`'s call order,
        median selection, and per-call session_id, independent of real hook
        subprocess timing — an off-by-one in the median index or a reversion
        to block-sequential sampling would otherwise hide behind this hook's
        own wide legitimate timing variance instead of failing reliably; a
        reused session_id would silently turn every sample but the first
        into a cache-hit read instead of the bootstrap scan this test means
        to time (see `_interleaved_median_seconds`'s docstring).
        """
        small_transcript = tmp_path / "small.jsonl"
        large_transcript = tmp_path / "large.jsonl"
        call_log: list[str] = []
        session_id_log: list[str] = []

        def fake_run_hook(payload, tmp_path_arg, extra_env=None):
            call_log.append(payload["transcript_path"])
            session_id_log.append(payload["session_id"])
            return subprocess.CompletedProcess(args=[], returncode=0)

        # small-arm calls take 0.3/0.1/0.2s in call order, large-arm calls
        # take 3.0/1.0/2.0s -- each arm's raw call-order middle value (0.1,
        # 1.0) differs from its true median (0.2, 2.0), so the assertions
        # below fail both if calls aren't truly alternating and if sorting
        # is skipped in favor of the chronologically-middle sample.
        durations = [0.3, 3.0, 0.1, 1.0, 0.2, 2.0]
        clock = iter(_perf_counter_sequence(durations))
        monkeypatch.setattr(time, "perf_counter", lambda: next(clock))
        monkeypatch.setattr(sys.modules[__name__], "_run_hook", fake_run_hook)

        small_median, large_median = _interleaved_median_seconds(
            small_transcript, large_transcript, tmp_path
        )

        assert call_log == [str(small_transcript), str(large_transcript)] * 3
        assert small_median == pytest.approx(0.2)
        assert large_median == pytest.approx(2.0)
        assert len(set(session_id_log)) == len(session_id_log), (
            "every call must get its own session_id -- a repeat would silently "
            "swap that sample's bootstrap scan for a free cache-hit read"
        )

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
        """135 000 (fired under the old flat 120 000 constant) is now silent on a 1M model — still
        below the 150000 absolute cap. Direct GH-556 regression test."""
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

    def test_advisory_context_still_carries_the_nearly_complete_escape_hatch(self, tmp_path):
        """The advisory path's additionalContext keeps the "nearly complete, ignore this" affordance —
        the hard-block path's own copy of it was removed on the assumption this is its only home."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(ABOVE_LARGE)])
        result = _run_hook(_base_payload(transcript), tmp_path)
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        assert "nearly complete" in ctx

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
        "malformed_value", ["abc", "080000", "", "0", "-1", "1.5", "1e5", "9223372036854775808"]
    )
    def test_abs_cap_malformed_override_falls_back_to_default_not_zero(self, tmp_path, malformed_value):
        """A malformed HANDOFF_NUDGE_ABS_CAP (non-numeric, zero-padded, empty, literal zero,
        negative, non-integer, or 10+ digits — which risks wrapping negative in bash's signed
        64-bit arithmetic, e.g. 2**63) must fall back to the shipped default rather than degrade
        THRESHOLD toward 0/unset/negative — which would fire on every session, the opposite of
        "override ignored". The bare "0" case needs its own explicit arm in the case pattern:
        0[0-9]* requires a second digit, so a lone "0" matches none of the other three arms
        without it."""
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
        "malformed_value", ["abc", "080000", "", "0", "-1", "1.5", "1e5", "9223372036854775808"]
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
    # HANDOFF_NUDGE_REARM_SPACING consumer override
    # -----------------------------------------------------------------------

    def test_rearm_spacing_override_changes_rearm_point(self, tmp_path):
        """A valid HANDOFF_NUDGE_REARM_SPACING overrides the default 80000-token
        spacing between fires. Pins HANDOFF_NUDGE_BLOCK_AFTER above the default
        so the third call's real re-arm (mechanics under test) stays advisory
        rather than exercising the escalation ladder."""
        custom_spacing = 20_000
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(LARGE_THRESHOLD, model="claude-sonnet-5")])
        extra_env = {
            "HANDOFF_NUDGE_REARM_SPACING": str(custom_spacing),
            "HANDOFF_NUDGE_BLOCK_AFTER": REARM_MECHANICS_BLOCK_AFTER,
        }
        first = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)
        assert first.stdout.strip() != ""

        below = LARGE_THRESHOLD + custom_spacing - 1
        _append_to_transcript(transcript, [_record_totalling(below, model="claude-sonnet-5")])
        result_below = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)
        assert result_below.stdout.strip() == ""

        at_spacing = LARGE_THRESHOLD + custom_spacing
        _append_to_transcript(transcript, [_record_totalling(at_spacing, model="claude-sonnet-5")])
        result_at = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)
        assert result_at.stdout.strip() != ""

    @pytest.mark.parametrize(
        "malformed_value", ["abc", "080000", "", "0", "-1", "1.5", "1e5", "9223372036854775808"]
    )
    def test_rearm_spacing_malformed_override_falls_back_to_default_not_zero(self, tmp_path, malformed_value):
        """A malformed HANDOFF_NUDGE_REARM_SPACING (non-numeric, zero-padded, empty, literal
        zero, negative, non-integer, or 10+ digits) must fall back to the shipped default
        rather than degrade REARM_SPACING toward 0/unset/negative -- which would fire
        on every turn, the opposite of "override ignored". The bare "0" case relies on the
        same explicit `0` case arm added alongside HANDOFF_NUDGE_ABS_CAP's guard."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(LARGE_THRESHOLD, model="claude-sonnet-5")])
        extra_env = {"HANDOFF_NUDGE_REARM_SPACING": malformed_value}
        first = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)
        assert first.stdout.strip() != ""

        second_estimate = LARGE_THRESHOLD + DEFAULT_REARM_SPACING - 1
        _append_to_transcript(transcript, [_record_totalling(second_estimate, model="claude-sonnet-5")])
        second = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)
        assert second.stdout.strip() == "", (
            f"malformed HANDOFF_NUDGE_REARM_SPACING={malformed_value!r} should fall back to "
            "the default spacing, not re-fire below it"
        )

    @pytest.mark.parametrize(
        "malformed_value", ["abc", "080000", "", "0", "-1", "1.5", "1e5", "9223372036854775808"]
    )
    def test_rearm_spacing_malformed_override_positive_control_fires_at_default(self, tmp_path, malformed_value):
        """Positive control for the test above: proves the fallback actually lands on
        DEFAULT_REARM_SPACING rather than some other silent-non-firing state that the
        negative-only test above cannot distinguish from a guard regression that
        leaves the hook permanently silent. Pins HANDOFF_NUDGE_BLOCK_AFTER above the
        default so the third call's real re-arm (mechanics under test) stays advisory
        rather than exercising the escalation ladder."""
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_record_totalling(LARGE_THRESHOLD, model="claude-sonnet-5")])
        extra_env = {
            "HANDOFF_NUDGE_REARM_SPACING": malformed_value,
            "HANDOFF_NUDGE_BLOCK_AFTER": REARM_MECHANICS_BLOCK_AFTER,
        }
        first = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)
        assert first.stdout.strip() != ""

        third_estimate = LARGE_THRESHOLD + DEFAULT_REARM_SPACING
        _append_to_transcript(transcript, [_record_totalling(third_estimate, model="claude-sonnet-5")])
        third = _run_hook(_base_payload(transcript), tmp_path, extra_env=extra_env)
        assert third.stdout.strip() != "", (
            f"malformed HANDOFF_NUDGE_REARM_SPACING={malformed_value!r} should fall back to "
            "the default spacing and fire at it, not stay silent indefinitely"
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

    def test_reports_status_when_neither_timeout_nor_gtimeout_present(self, tmp_path):
        """Fail-open regression for --check's own _lib_capped_for call sites
        (check_refuse, the schema-drift build, and the final status:ok build):
        with neither binary present, run_check_mode must still return a
        status field rather than a silent/empty result."""
        self._seeded(tmp_path, total=ABOVE_LARGE)
        fake_bin = tmp_path / "fakebin-no-timeout-no-gtimeout"
        fake_bin.mkdir()
        restricted_path = _check_mode_path_without_timeout_or_gtimeout(fake_bin)
        payload = _check_json(_run_check(tmp_path, extra_env={"PATH": restricted_path}))
        assert payload["status"] == "ok"
        assert payload["over_threshold"] is True

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

    def test_already_fired_stays_true_even_when_due_for_rearm(self, tmp_path):
        """already_fired reports marker existence only, not "no further nudge is
        due" -- pins the contract docs/handoff-nudge.md's JSON-contract section now
        documents: a session past its next re-arm band is still already_fired=true,
        since --check doesn't compute a rearm-due boolean at all."""
        config_dir = self._seeded(tmp_path, total=LARGE_THRESHOLD + DEFAULT_REARM_SPACING)
        marker = _marker_path(tmp_path, config_dir=config_dir)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"{LARGE_THRESHOLD}\n")
        payload = _check_json(_run_check(tmp_path))
        assert payload["status"] == "ok"
        assert payload["already_fired"] is True
        assert payload["over_threshold"] is True

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
