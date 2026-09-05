"""Tests for log-reviewer-round.sh."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
from helpers import (
    CLAUDE_DIR,
    CONSULT_CLASSIFICATION_TABLE,
    HOOKS_DIR,
    agent_input,
    architect_consult_latch_path,
    bash_input,
    reviewer_round_state_path,
    reviewer_round_state_value,
    run_hook_advisory,
    write_reviewer_round_state,
)

from .conftest import _dead_pid

LOG_REVIEWER_ROUND_HOOK = HOOKS_DIR / "log-reviewer-round.sh"
PLAN_ARCHITECT_AGENT = CLAUDE_DIR / "agents" / "plan-architect.md"

REVIEWER_PERSONA = "staff-backend-engineer"

# Generous bound for the N-way concurrent-dispatch test: every racer's own
# guard call is a handful of _lib_capped (5s-backstopped) git subprocesses
# plus the append lock's own bounded retries, so a loaded CI runner can take
# several seconds per racer under heavy N-way contention without any racer
# being genuinely hung.
_CONCURRENT_DISPATCH_TIMEOUT_SECONDS = 30


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "f.txt").write_text("first\n")
    subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)


def _stage_change(repo: Path, content: str) -> None:
    (repo / "f.txt").write_text(content)
    subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True)


def _commit_staged(repo: Path) -> None:
    subprocess.run(["git", "commit", "-q", "-m", "wip"], cwd=repo, check=True)


def _run_hook_raw(payload: dict, cwd: Path, home: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(home)}
    env.pop("CLAUDE_CONFIG_DIR", None)
    return subprocess.run(
        [str(LOG_REVIEWER_ROUND_HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        check=False,
    )


# Wall-clock bound for the mkdir-interception barrier below: a busy-spin
# wait, checked against bash's $SECONDS rather than a sleep-based poll, so
# every racer notices the release within sub-millisecond precision instead
# of a sleep interval's worth of staggered wakeups (confirmed empirically:
# a sleep-based poll release reintroduces enough skew that the racers stop
# colliding at the critical section, defeating the barrier's purpose).
# 40s matches test_lib_worktree_collision_guard.py's own
# _RACER_BARRIER_MAX_WAIT_SECONDS precedent for a CPU-contended N-way
# racer barrier under a loaded -n auto run.
_RACER_BARRIER_MAX_WAIT_SECONDS = 40


def _write_mkdir_barrier_shim(
    bin_dir: Path, parked_dir: Path, release_flag: Path, concurrency: int
) -> None:
    """Write an `mkdir` shim onto bin_dir: each invocation touches
    parked_dir/$$ (its own pid), then busy-spins until release_flag exists
    before proceeding.

    `_record_reviewer_round` (log-reviewer-round.sh) calls
    `mkdir -p "$state_dir"` as its last external command before the
    check-then-append critical section `_lib_append_line_locked` guards.
    Intercepting that one call, rather than the hook's own entry point, is
    what actually closes the race window: a barrier held at hook-invocation
    time still lets each racer's own upstream dispatch latency (forking
    bash, `git rev-parse`, `git diff --cached`, `sha256sum`, `jq`) spread
    arrival at the real critical section wide enough that racers never
    collide there in practice, even though nothing serializes them --
    confirmed empirically: a park-then-release barrier applied before the
    hook call, rather than here, left this test passing even with
    `_lib_append_line_locked`'s locking entirely stripped out.

    Once released, the shim skips calling real mkdir(1) whenever every
    target directory already exists, returning immediately instead --
    the caller MUST pre-create $state_dir for this to fire. exec'ing a real
    external mkdir binary after release reintroduces the same kind of
    scheduling variance the barrier exists to eliminate (confirmed
    empirically), since loading and running a second binary image takes
    long enough, and varies enough run to run, to blow the critical
    section's own sub-millisecond collision window back open. mkdir -p
    against an already-existing directory is a no-op regardless, so
    skipping the real call changes no observable behavior.

    Mirrors test_lib_worktree_collision_guard.py's
    _launch_collision_guard_racer barrier idiom (touch a per-pid marker,
    then wait on a shared release signal with a bounded, distinctly-coded
    timeout), anchored at the hook's last pre-critical-section external
    call instead of at process launch.

    parked_dir and release_flag's parent must already exist -- creating
    them via a shimmed `mkdir` would recurse.
    """
    real_mkdir = shutil.which("mkdir") or "/bin/mkdir"
    shim = bin_dir / "mkdir"
    shim.write_text(
        "#!/bin/bash\n"
        f': > "{parked_dir}/$$"\n'
        'start=$SECONDS\n'
        f'while [ ! -f "{release_flag}" ]; do\n'
        f'  if [ $((SECONDS - start)) -gt {_RACER_BARRIER_MAX_WAIT_SECONDS} ]; then\n'
        f'    echo "mkdir-barrier-shim $$: barrier never satisfied after '
        f'{_RACER_BARRIER_MAX_WAIT_SECONDS}s" >&2\n'
        '    exit 5\n'
        '  fi\n'
        'done\n'
        'for target in "$@"; do\n'
        '  case "$target" in\n'
        '    -*) continue ;;\n'
        f'    *) [ -d "$target" ] || exec {shlex.quote(str(real_mkdir))} "$@" ;;\n'
        '  esac\n'
        'done\n'
        'exit 0\n'
    )
    shim.chmod(0o755)


def _launch_hook_with_path_prefix(
    payload: dict, cwd: Path, home: Path, path_prefix: Path
) -> subprocess.Popen:
    """Like the plain hook launches elsewhere in this file, but prepends
    path_prefix to PATH, for racing against a PATH-shimmed command (see
    _write_mkdir_barrier_shim). Writes the payload into a temp file, flushes
    and rewinds it, then hands that file to Popen as stdin, returning
    without waiting for exit so N launches can be in flight concurrently.
    The write/flush/seek(0) must happen before Popen() is called, not
    after, because Popen dup2's the fd at construction time and shares the
    parent's file offset."""
    env = {**os.environ, "HOME": str(home)}
    env.pop("CLAUDE_CONFIG_DIR", None)
    env["PATH"] = f"{path_prefix}:{env['PATH']}"
    # TemporaryFile, not PIPE: on CPython 3.12 (CI's pinned version), closed-PIPE
    # stdin makes Popen.communicate() raise ValueError, and file-backed stdin
    # avoids it by leaving Popen.stdin None.
    payload_stdin = tempfile.TemporaryFile("w+")
    payload_stdin.write(json.dumps(payload))
    payload_stdin.flush()
    payload_stdin.seek(0)
    try:
        proc = subprocess.Popen(
            [str(LOG_REVIEWER_ROUND_HOOK)],
            stdin=payload_stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            env=env,
        )
    finally:
        payload_stdin.close()
    return proc


class TestLaunchHookWithPathPrefixStdinContract:
    """Pins the file-backed stdin invariant _launch_hook_with_path_prefix
    relies on. It is isolated from the hook script, git repo setup, and
    test_n_concurrent_dispatches_at_same_state_produce_exactly_one_entry
    (TestLogReviewerRoundConcurrency), which is flaky under N-way
    concurrency. It guarantees that a TemporaryFile-backed stdin, flushed
    and rewound before Popen(), lets communicate() run without raising and
    delivers the full payload to the child."""

    def test_communicate_does_not_raise_and_child_reads_full_payload(self):
        payload = "hello from a file-backed stdin\n"
        stdin_file = tempfile.TemporaryFile("w+")
        stdin_file.write(payload)
        stdin_file.flush()
        stdin_file.seek(0)
        try:
            proc = subprocess.Popen(
                ["cat"],
                stdin=stdin_file,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        finally:
            stdin_file.close()

        stdout, stderr = proc.communicate()

        assert proc.returncode == 0, f"cat failed: {stderr}"
        assert stdout == payload


class TestLogReviewerRoundStateAppend:
    def test_first_dispatch_creates_file_with_one_line(self, isolated_home, tmp_path):
        repo = tmp_path / "first-dispatch"
        _init_repo(repo)
        _stage_change(repo, "first\nround-one\n")
        value = reviewer_round_state_value(repo)
        payload = agent_input(session_id="s-first", subagent_type=REVIEWER_PERSONA)
        run_hook_advisory(LOG_REVIEWER_ROUND_HOOK, payload, cwd=repo, home=isolated_home)
        state_file = reviewer_round_state_path(isolated_home / ".claude", repo)
        assert state_file.read_text().splitlines() == [value]

    def test_second_dispatch_same_state_does_not_duplicate(self, isolated_home, tmp_path):
        repo = tmp_path / "dedup"
        _init_repo(repo)
        _stage_change(repo, "first\nround-one\n")
        payload = agent_input(session_id="s-dedup", subagent_type=REVIEWER_PERSONA)
        run_hook_advisory(LOG_REVIEWER_ROUND_HOOK, payload, cwd=repo, home=isolated_home)
        run_hook_advisory(LOG_REVIEWER_ROUND_HOOK, payload, cwd=repo, home=isolated_home)
        state_file = reviewer_round_state_path(isolated_home / ".claude", repo)
        assert len(state_file.read_text().splitlines()) == 1

    def test_distinct_state_appends(self, isolated_home, tmp_path):
        repo = tmp_path / "append-distinct"
        _init_repo(repo)
        _stage_change(repo, "first\nround-one\n")
        value1 = reviewer_round_state_value(repo)
        payload = agent_input(session_id="s-distinct", subagent_type=REVIEWER_PERSONA)
        run_hook_advisory(LOG_REVIEWER_ROUND_HOOK, payload, cwd=repo, home=isolated_home)

        _commit_staged(repo)
        value2 = reviewer_round_state_value(repo)
        run_hook_advisory(LOG_REVIEWER_ROUND_HOOK, payload, cwd=repo, home=isolated_home)

        state_file = reviewer_round_state_path(isolated_home / ".claude", repo)
        assert state_file.read_text().splitlines() == [value1, value2]

    def test_file_never_exceeds_cap(self, isolated_home, tmp_path):
        repo = tmp_path / "cap-test"
        _init_repo(repo)
        _stage_change(repo, "first\nround-one\n")
        value1 = reviewer_round_state_value(repo)
        _commit_staged(repo)
        value2 = reviewer_round_state_value(repo)
        state_file = write_reviewer_round_state(isolated_home / ".claude", repo, [value1, value2])

        _stage_change(repo, "first\nround-three\n")
        payload = agent_input(session_id="s-cap", subagent_type=REVIEWER_PERSONA)
        run_hook_advisory(LOG_REVIEWER_ROUND_HOOK, payload, cwd=repo, home=isolated_home)

        assert state_file.read_text().splitlines() == [value1, value2]

    def test_latch_short_circuits_further_appends(self, isolated_home, tmp_path):
        """Once the latch exists for this branch, the recorder skips round
        tracking entirely -- even below the cap, where a broken
        short-circuit would otherwise show up as an ordinary append."""
        repo = tmp_path / "latch-shortcircuit"
        _init_repo(repo)
        _stage_change(repo, "first\nround-one\n")
        value1 = reviewer_round_state_value(repo)
        state_file = write_reviewer_round_state(isolated_home / ".claude", repo, [value1])

        latch = architect_consult_latch_path(isolated_home / ".claude", repo)
        latch.parent.mkdir(parents=True, exist_ok=True)
        latch.touch()

        _stage_change(repo, "first\nround-one\nround-two\n")
        payload = agent_input(session_id="s-shortcircuit", subagent_type=REVIEWER_PERSONA)
        run_hook_advisory(LOG_REVIEWER_ROUND_HOOK, payload, cwd=repo, home=isolated_home)

        assert state_file.read_text().splitlines() == [value1]

    def test_live_plan_review_active_marker_skips_recording(self, isolated_home, tmp_path):
        """A live /plan-review fan-out must not consume a round-counting
        slot."""
        repo = tmp_path / "plan-review-active-skip"
        _init_repo(repo)
        _stage_change(repo, "first\nround-one\n")
        sid = "s-plan-review-active"
        marker_dir = isolated_home / ".claude" / ".plan-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).write_text(str(os.getpid()))
        payload = agent_input(session_id=sid, subagent_type=REVIEWER_PERSONA)
        run_hook_advisory(LOG_REVIEWER_ROUND_HOOK, payload, cwd=repo, home=isolated_home)
        state_file = reviewer_round_state_path(isolated_home / ".claude", repo)
        assert not state_file.exists()

    def test_live_ready_for_review_active_marker_skips_recording(self, isolated_home, tmp_path):
        repo = tmp_path / "ready-for-review-active-skip"
        _init_repo(repo)
        _stage_change(repo, "first\nround-one\n")
        sid = "s-ready-for-review-active"
        marker_dir = isolated_home / ".claude" / ".ready-for-review-active.d"
        marker_dir.mkdir(parents=True)
        (marker_dir / sid).write_text(str(os.getpid()))
        payload = agent_input(session_id=sid, subagent_type=REVIEWER_PERSONA)
        run_hook_advisory(LOG_REVIEWER_ROUND_HOOK, payload, cwd=repo, home=isolated_home)
        state_file = reviewer_round_state_path(isolated_home / ".claude", repo)
        assert not state_file.exists()

    def test_non_reviewer_subagent_type_is_noop(self, isolated_home, tmp_path):
        repo = tmp_path / "non-reviewer"
        _init_repo(repo)
        _stage_change(repo, "first\nround-one\n")
        payload = agent_input(session_id="s-code-writer", subagent_type="code-writer")
        run_hook_advisory(LOG_REVIEWER_ROUND_HOOK, payload, cwd=repo, home=isolated_home)
        state_file = reviewer_round_state_path(isolated_home / ".claude", repo)
        assert not state_file.exists()

    def test_non_agent_task_tool_name_is_noop(self, isolated_home, tmp_path):
        """Defense-in-depth: a non-Agent/Task tool call is never recorded,
        even carrying a reviewer-persona-shaped payload."""
        repo = tmp_path / "wrong-tool-name"
        _init_repo(repo)
        _stage_change(repo, "first\nround-one\n")
        run_hook_advisory(
            LOG_REVIEWER_ROUND_HOOK, bash_input("ls", session_id="s-wrong-tool"), cwd=repo, home=isolated_home
        )
        state_file = reviewer_round_state_path(isolated_home / ".claude", repo)
        assert not state_file.exists()


class TestLogReviewerRoundPayloadCwd:
    def test_payload_cwd_field_resolves_state_over_subprocess_pwd(self, isolated_home, tmp_path):
        """CWD=$(jq -r '.cwd // empty' ...) inside _resolve_round_context is
        the primary CWD-resolution signal, falling back to $PWD only when
        the payload carries no `.cwd`. Runs the subprocess from a neutral
        non-repo directory, but passes the real repo via the JSON `cwd`
        field: a hook that (wrongly) read $PWD instead would find no git
        repo there and record nothing, so asserting the state file lands at
        the real repo's key pins that `.cwd` -- not $PWD -- is what actually
        resolves the repo root."""
        repo = tmp_path / "payload-cwd-recorder"
        _init_repo(repo)
        _stage_change(repo, "first\nround-one\n")
        value = reviewer_round_state_value(repo)
        neutral_cwd = tmp_path / "neutral-cwd"
        neutral_cwd.mkdir()
        payload = agent_input(session_id="s-payload-cwd", subagent_type=REVIEWER_PERSONA, cwd=str(repo))
        run_hook_advisory(LOG_REVIEWER_ROUND_HOOK, payload, cwd=neutral_cwd, home=isolated_home)
        state_file = reviewer_round_state_path(isolated_home / ".claude", repo)
        assert state_file.read_text().splitlines() == [value]


class TestLogReviewerRoundConsultLatch:
    def test_mode_consult_writes_latch(self, isolated_home, tmp_path):
        repo = tmp_path / "latch-consult"
        _init_repo(repo)
        payload = agent_input(
            session_id="s-latch-consult",
            subagent_type="plan-architect",
            prompt="MODE=consult\nIs the foundation wrong?",
        )
        run_hook_advisory(LOG_REVIEWER_ROUND_HOOK, payload, cwd=repo, home=isolated_home)
        assert architect_consult_latch_path(isolated_home / ".claude", repo).exists()

    def test_absent_mode_line_writes_latch(self, isolated_home, tmp_path):
        """Fail-safe direction: a dispatch carrying neither MODE= line is a
        consult (plan-architect.md)."""
        repo = tmp_path / "latch-absent-mode"
        _init_repo(repo)
        payload = agent_input(
            session_id="s-latch-absent",
            subagent_type="plan-architect",
            prompt="Just look at the plan and tell me if it's sound.",
        )
        run_hook_advisory(LOG_REVIEWER_ROUND_HOOK, payload, cwd=repo, home=isolated_home)
        assert architect_consult_latch_path(isolated_home / ".claude", repo).exists()

    def test_mode_plan_sections_does_not_write_latch(self, isolated_home, tmp_path):
        repo = tmp_path / "latch-plan-sections"
        _init_repo(repo)
        payload = agent_input(
            session_id="s-latch-plan-sections",
            subagent_type="plan-architect",
            prompt="MODE=plan-sections\nSection A: Approach",
        )
        run_hook_advisory(LOG_REVIEWER_ROUND_HOOK, payload, cwd=repo, home=isolated_home)
        assert not architect_consult_latch_path(isolated_home / ".claude", repo).exists()

    def test_arbitrary_nonmatching_mode_value_still_writes_latch(self, isolated_home, tmp_path):
        """Pins the fail-safe direction at more than its two literal
        endpoints (absent / exact plan-sections): a typo'd MODE= value is
        still treated as a consult, not silently ignored."""
        repo = tmp_path / "latch-mode-typo"
        _init_repo(repo)
        payload = agent_input(
            session_id="s-latch-typo",
            subagent_type="plan-architect",
            prompt="MODE=plna-sections\noops, typo'd",
        )
        run_hook_advisory(LOG_REVIEWER_ROUND_HOOK, payload, cwd=repo, home=isolated_home)
        assert architect_consult_latch_path(isolated_home / ".claude", repo).exists()

    def test_non_plan_architect_subagent_type_never_writes_latch(self, isolated_home, tmp_path):
        repo = tmp_path / "latch-not-architect"
        _init_repo(repo)
        payload = agent_input(
            session_id="s-not-architect",
            subagent_type=REVIEWER_PERSONA,
            prompt="MODE=consult",
        )
        run_hook_advisory(LOG_REVIEWER_ROUND_HOOK, payload, cwd=repo, home=isolated_home)
        assert not architect_consult_latch_path(isolated_home / ".claude", repo).exists()

    @pytest.mark.parametrize(
        "first_line,expect_consult",
        [pytest.param(fl, ec, id=tid) for fl, ec, tid in CONSULT_CLASSIFICATION_TABLE],
    )
    def test_latch_classification_matches_table(
        self, isolated_home, tmp_path, first_line, expect_consult
    ):
        repo = tmp_path / "latch-parity"
        _init_repo(repo)
        payload = agent_input(
            session_id="s-latch-parity",
            subagent_type="plan-architect",
            prompt=first_line,
        )
        run_hook_advisory(LOG_REVIEWER_ROUND_HOOK, payload, cwd=repo, home=isolated_home)
        latch_exists = architect_consult_latch_path(isolated_home / ".claude", repo).exists()
        assert latch_exists is expect_consult


class TestPlanArchitectModeLiteralTripwire:
    def test_mode_plan_sections_heading_present(self):
        """A wiring-presence tripwire only -- plan-architect.md is
        LLM-consumed prose with no runtime to assert behavior against.
        Anchored on the `## MODE=plan-sections` section heading, the more
        structurally stable of the file's two occurrences of the literal,
        not the prose sentence describing it."""
        body = PLAN_ARCHITECT_AGENT.read_text()
        assert "## MODE=plan-sections" in body


class TestLogReviewerRoundConcurrency:
    def test_n_concurrent_dispatches_at_same_state_produce_exactly_one_entry(
        self, isolated_home, tmp_path
    ):
        """The exact race _lib_append_line_locked's lock exists to close: N
        concurrent reviewer-persona dispatches against the identical (head,
        staged-diff) state -- a real /code-review parallel fan-out -- must
        collapse to exactly one recorded round, not one per racer. A
        PATH-shimmed `mkdir` (see _write_mkdir_barrier_shim) parks every
        racer immediately before the hook's check-then-append critical
        section and releases them together via a single shared flag, so
        they collide there instead of arriving spread across each racer's
        own dispatch latency."""
        repo = tmp_path / "concurrent-same-state"
        _init_repo(repo)
        _stage_change(repo, "first\nround-one\n")
        payload = agent_input(session_id="s-concurrent", subagent_type=REVIEWER_PERSONA)

        state_file = reviewer_round_state_path(isolated_home / ".claude", repo)
        state_file.parent.mkdir(parents=True, exist_ok=True)

        concurrency = 15
        bin_dir = tmp_path / "shim-bin"
        bin_dir.mkdir()
        parked_dir = tmp_path / "racer-parked"
        parked_dir.mkdir()
        release_flag = tmp_path / "release.flag"
        _write_mkdir_barrier_shim(bin_dir, parked_dir, release_flag, concurrency)

        procs = [
            _launch_hook_with_path_prefix(payload, repo, isolated_home, bin_dir)
            for _ in range(concurrency)
        ]

        deadline = time.monotonic() + _RACER_BARRIER_MAX_WAIT_SECONDS
        while len(list(parked_dir.iterdir())) < concurrency:
            if time.monotonic() > deadline:
                pytest.fail(
                    f"only {len(list(parked_dir.iterdir()))}/{concurrency} racers "
                    f"parked within {_RACER_BARRIER_MAX_WAIT_SECONDS}s -- a racer "
                    f"never reached the barrier (dispatch failure), not a race outcome"
                )
        release_flag.touch()

        for proc in procs:
            try:
                _, stderr = proc.communicate(timeout=_CONCURRENT_DISPATCH_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                raise
            assert proc.returncode == 0, f"racer failed: {stderr}"

        assert state_file.read_text().splitlines() == [reviewer_round_state_value(repo)]

    def test_stale_lock_held_by_dead_pid_is_evicted_and_append_proceeds(
        self, isolated_home, tmp_path
    ):
        """Simulates a hook killed mid-lock (harness timeout): a lock file
        whose stored PID is dead must be evicted immediately, not block the
        append for the remaining bounded retries."""
        repo = tmp_path / "stale-lock"
        _init_repo(repo)
        _stage_change(repo, "first\nround-one\n")
        value = reviewer_round_state_value(repo)

        state_file = reviewer_round_state_path(isolated_home / ".claude", repo)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        lock_file = state_file.parent / f"{state_file.name}.lock"
        lock_file.write_text(str(_dead_pid()))

        payload = agent_input(session_id="s-stale-lock", subagent_type=REVIEWER_PERSONA)
        run_hook_advisory(LOG_REVIEWER_ROUND_HOOK, payload, cwd=repo, home=isolated_home)

        assert state_file.read_text().splitlines() == [value]
        assert not lock_file.exists()

    def test_live_pid_lock_falls_through_to_unlocked_append(
        self, isolated_home, tmp_path, live_pid
    ):
        """A lock held by a LIVE pid for the whole call is never evicted,
        unlike the dead-pid case above -- the caller instead falls through
        to an unlocked append rather than hanging or dropping the line."""
        repo = tmp_path / "live-lock-fallthrough"
        _init_repo(repo)
        _stage_change(repo, "first\nround-one\n")
        value = reviewer_round_state_value(repo)

        state_file = reviewer_round_state_path(isolated_home / ".claude", repo)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        lock_file = state_file.parent / f"{state_file.name}.lock"
        lock_file.write_text(str(live_pid))

        payload = agent_input(session_id="s-live-lock-retry", subagent_type=REVIEWER_PERSONA)
        run_hook_advisory(LOG_REVIEWER_ROUND_HOOK, payload, cwd=repo, home=isolated_home)

        assert state_file.read_text().splitlines() == [value]


class TestLogReviewerRoundSweep:
    def test_stale_entries_swept_fresh_entries_spared(self, isolated_home, tmp_path):
        stale_repo = tmp_path / "stale-repo"
        _init_repo(stale_repo)
        _stage_change(stale_repo, "first\nstale\n")
        stale_value = reviewer_round_state_value(stale_repo)
        stale_file = write_reviewer_round_state(isolated_home / ".claude", stale_repo, [stale_value])
        old_time = time.time() - (31 * 86400)
        os.utime(stale_file, (old_time, old_time))

        fresh_repo = tmp_path / "fresh-repo"
        _init_repo(fresh_repo)
        _stage_change(fresh_repo, "first\nfresh\n")
        payload = agent_input(session_id="s-fresh", subagent_type=REVIEWER_PERSONA)
        run_hook_advisory(LOG_REVIEWER_ROUND_HOOK, payload, cwd=fresh_repo, home=isolated_home)

        assert not stale_file.exists()
        fresh_file = reviewer_round_state_path(isolated_home / ".claude", fresh_repo)
        assert fresh_file.exists()

    def test_stale_latch_swept_fresh_latch_spared(self, isolated_home, tmp_path):
        """_maybe_write_consult_latch's own 30-day sweep, structurally
        identical to _record_reviewer_round's sweep above but over
        .architect-consult-latch.d rather than .reviewer-round-state.d."""
        stale_repo = tmp_path / "stale-latch-repo"
        _init_repo(stale_repo)
        stale_latch = architect_consult_latch_path(isolated_home / ".claude", stale_repo)
        stale_latch.parent.mkdir(parents=True, exist_ok=True)
        stale_latch.touch()
        old_time = time.time() - (31 * 86400)
        os.utime(stale_latch, (old_time, old_time))

        fresh_repo = tmp_path / "fresh-latch-repo"
        _init_repo(fresh_repo)
        payload = agent_input(
            session_id="s-fresh-latch",
            subagent_type="plan-architect",
            prompt="MODE=consult\nIs the foundation wrong?",
        )
        run_hook_advisory(LOG_REVIEWER_ROUND_HOOK, payload, cwd=fresh_repo, home=isolated_home)

        assert not stale_latch.exists()
        fresh_latch = architect_consult_latch_path(isolated_home / ".claude", fresh_repo)
        assert fresh_latch.exists()


class TestLogReviewerRoundFailureDegradesCleanly:
    def test_unresolvable_repo_root_is_clean_noop(self, isolated_home, tmp_path):
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        payload = agent_input(session_id="s-no-repo", subagent_type=REVIEWER_PERSONA)
        result = _run_hook_raw(payload, non_repo, isolated_home)
        assert result.returncode == 0
        assert result.stdout == ""
        assert not (isolated_home / ".claude" / ".reviewer-round-state.d").exists()

    def test_detached_head_is_clean_noop(self, isolated_home, tmp_path):
        repo = tmp_path / "detached"
        _init_repo(repo)
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        subprocess.run(["git", "checkout", "-q", sha], cwd=repo, check=True)
        payload = agent_input(session_id="s-detached", subagent_type=REVIEWER_PERSONA)
        result = _run_hook_raw(payload, repo, isolated_home)
        assert result.returncode == 0
        assert result.stdout == ""
        assert not (isolated_home / ".claude" / ".reviewer-round-state.d").exists()

    def test_unresolvable_config_dir_is_clean_noop(self, isolated_home, tmp_path):
        repo = tmp_path / "bad-config-dir"
        _init_repo(repo)
        _stage_change(repo, "first\nround-one\n")
        payload = agent_input(session_id="s-bad-config-dir", subagent_type=REVIEWER_PERSONA)
        env = {**os.environ, "HOME": str(isolated_home), "CLAUDE_CONFIG_DIR": "relative/path"}
        result = subprocess.run(
            [str(LOG_REVIEWER_ROUND_HOOK)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            cwd=repo,
            env=env,
            check=False,
        )
        assert result.returncode == 0
        assert result.stdout == ""
        assert not (isolated_home / ".claude" / ".reviewer-round-state.d").exists()
