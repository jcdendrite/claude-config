"""hook-shim.py: the hook run, its peak memory under endless output, and its in-process rows with a stand-in for Popen."""

from __future__ import annotations

import contextlib
import errno
import json
import os
import platform
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from helpers import CLAUDE_DIR

from ._cursor_shim_helpers import (
    HOOK_STARTED_THEN_SLEEP,
    HOOK_STARTED_TOUCH,
    OUTPUT_CAP,
    PAYLOAD_CAP,
    SHIM_MODULE,
    _assert_hook_started,
    _probe_ran,
    _recorded_wrapper,
    _scratch,
    _workspace,
)

sys.path.insert(0, str(CLAUDE_DIR))
from hooks.tests._cursor_payloads import (  # noqa: E402 (the import needs the sys.path entry above)
    hook_args,
    make_shim_env,
    read_log_lines,
    recording_probe_script,
    run_audited,
    run_shim,
    shell_pre_payload,
    shim_argv,
    shim_json,
    shim_process_env,
    write_hook_script,
)

# --- the hook run ---


# Longer than the 300 runs of the contention row take, so the hogs never expire mid-row.
CONTENTION_HOG_LIFETIME_SECONDS = 300


@pytest.fixture
def gnu_wrapper() -> str:
    return _recorded_wrapper()


def _timed_run(tmp_path: Path, body: str, *, deadline: int, payload: bytes | dict | None = None, fail_closed: bool = True):
    """Runs the shim on a hook with `body`. Returns (the decoded output document, the elapsed seconds, the env)."""
    env = make_shim_env(tmp_path)
    script = write_hook_script(tmp_path / "hook.sh", body)
    data = payload if payload is not None else shell_pre_payload(_workspace(tmp_path), "echo hi")
    args = hook_args("PreToolUse", script=script, matcher="Bash", fail_closed=fail_closed, deadline=deadline)
    started = time.monotonic()
    result = run_shim(env, data, args, cwd=_scratch(tmp_path))
    elapsed = time.monotonic() - started
    assert result.returncode == 0, result.stderr
    return shim_json(result), elapsed, env


def _timing_bound(deadline: int) -> float:
    return SHIM_MODULE.worst_case_wall_seconds(deadline) + SHIM_MODULE.MARGIN_SECONDS


def _big_command_payload(tmp_path: Path, size: int) -> dict:
    return shell_pre_payload(_workspace(tmp_path), "x" * size)


class _Clock:
    """A stand-in for the `time` module the shim reads: monotonic() returns the next scripted value, then the last one."""

    def __init__(self, *values: float) -> None:
        self.values = list(values)

    def monotonic(self) -> float:
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]

    def time(self) -> float:
        return 0.0


class _StandInProcess:
    """A recording stand-in for Popen: real pipes, a scripted return code, and a record of every call a test forbids."""

    def __init__(
        self, argv, *, returncode=0, emit_stdout=b"", emit_stderr=b"", hold_open=False, wait_raises=False, on_wait=None, **kwargs
    ):
        self.argv, self.kwargs = argv, kwargs
        self.returncode = None
        self._status = returncode
        self._wait_raises = wait_raises
        self._on_wait = on_wait
        self.calls: list[tuple] = []
        self._held: list[int] = []
        out_read, out_write = os.pipe()
        err_read, err_write = os.pipe()
        in_read, in_write = os.pipe()
        os.close(in_read)
        for data, descriptor in ((emit_stdout, out_write), (emit_stderr, err_write)):
            os.write(descriptor, data)
            if hold_open:
                self._held.append(descriptor)
            else:
                os.close(descriptor)
        self.stdout = os.fdopen(out_read, "rb", buffering=0)
        self.stderr = os.fdopen(err_read, "rb", buffering=0)
        self.stdin = os.fdopen(in_write, "wb", buffering=0)

    def wait(self, timeout=None):
        self.calls.append(("wait", timeout))
        if self._on_wait:
            self._on_wait()
        if self._wait_raises:
            raise subprocess.TimeoutExpired(self.argv, timeout)
        self.returncode = self._status
        return self.returncode

    def __getattr__(self, name):
        if name in ("kill", "terminate", "send_signal", "poll"):
            return lambda *args: self.calls.append((name, *args))
        raise AttributeError(name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.calls.append(("__exit__",))

    def release(self) -> None:
        for descriptor in self._held:
            os.close(descriptor)
        self._held.clear()


@pytest.fixture
def stand_in(monkeypatch: pytest.MonkeyPatch):
    """Replaces Popen with a recording stand-in. `make(**options)` sets how the next start behaves. Returns (made, starts)."""
    made: list[_StandInProcess] = []
    options: dict = {}

    def factory(argv, **kwargs):
        if "raises" in options:
            raise options["raises"]
        process = _StandInProcess(argv, **kwargs, **options)
        made.append(process)
        return process

    monkeypatch.setattr(SHIM_MODULE.subprocess, "Popen", factory)
    yield made, options
    for process in made:
        process.release()


def _call_run_wrapped(deadline_at: float = 8.0, directory: str = "/", wrapper: str = "/recorded/timeout"):
    return SHIM_MODULE.run_wrapped_hook(wrapper, ["/hook.sh"], b'{"k":1}', directory, {}, deadline_at)


class TestShimHookRun:
    @pytest.mark.timing
    def test_a_hook_that_prints_a_deny_and_exits_at_once_denies_every_time_under_cpu_contention(
        self, tmp_path: Path, gnu_wrapper: str
    ) -> None:
        # xdist sets PYTEST_XDIST_WORKER in each worker process and nowhere else, and the hogs take every core.
        if os.environ.get("PYTEST_XDIST_WORKER") is not None:
            pytest.skip("starts one busy process per core, so it runs only in a serial session (-n0)")
        deny = json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "no",
                }
            }
        )
        env = make_shim_env(tmp_path)
        script = write_hook_script(tmp_path / "hook.sh", f"printf '%s' '{deny}'\n")
        args = hook_args("PreToolUse", script=script, matcher="Bash", deadline=10)
        payload = shell_pre_payload(_workspace(tmp_path), "echo hi")
        # Each hog expires on its own, so a pytest killed mid-row does not leave processes spinning on every core.
        spin = f"import time\nend = time.monotonic() + {CONTENTION_HOG_LIFETIME_SECONDS}\nwhile time.monotonic() < end: pass"
        hogs = [subprocess.Popen([sys.executable, "-c", spin]) for _ in range(os.cpu_count() or 2)]
        try:
            for _ in range(300):
                output = shim_json(run_shim(env, payload, args, cwd=_scratch(tmp_path)))
                assert output["permission"] == "deny"
                assert output["agent_message"] == "no"
        finally:
            for hog in hogs:
                hog.kill()
                hog.wait()

    def test_a_hook_that_writes_more_than_a_pipes_capacity_before_it_reads_a_large_stdin_finishes(
        self, tmp_path: Path, gnu_wrapper: str
    ) -> None:
        body = "head -c 200000 /dev/zero | tr '\\0' ' '\nhead -c 200000 /dev/zero | tr '\\0' ' ' >&2\ncat > /dev/null\n"
        output, _, _ = _timed_run(tmp_path, body, deadline=10, payload=_big_command_payload(tmp_path, 1024 * 1024))
        assert output == {}

    def test_a_hook_that_exits_zero_without_reading_eight_mebibytes_gives_exit_zeros_outcome(
        self, tmp_path: Path, gnu_wrapper: str
    ) -> None:
        output, _, env = _timed_run(tmp_path, "exit 0\n", deadline=10, payload=_big_command_payload(tmp_path, PAYLOAD_CAP - 8192))
        assert output == {}
        assert read_log_lines(env)[-1]["exit"] == "0"

    @pytest.mark.timing
    def test_a_hook_that_never_reads_its_stdin_and_sleeps_past_the_deadline_takes_the_timeout_outcome(
        self, tmp_path: Path, gnu_wrapper: str
    ) -> None:
        output, elapsed, env = _timed_run(
            tmp_path, HOOK_STARTED_THEN_SLEEP, deadline=2, payload=_big_command_payload(tmp_path, 1024 * 1024)
        )
        _assert_hook_started(tmp_path)
        assert "outcome timeout" in output["agent_message"]
        assert elapsed < _timing_bound(2)
        assert read_log_lines(env)[-1]["out"] == "timeout"

    @pytest.mark.timing
    def test_a_sender_that_never_closes_stdin_takes_the_timeout_outcome_and_no_hook_starts(self, tmp_path: Path) -> None:
        env = make_shim_env(tmp_path)
        script = recording_probe_script(tmp_path / "hook.sh", tmp_path / "saved.json")
        process = subprocess.Popen(
            shim_argv(*hook_args("PreToolUse", script=script, deadline=2)),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=_scratch(tmp_path),
            env=shim_process_env(env),
        )
        started = time.monotonic()
        try:
            process.stdin.write(json.dumps(shell_pre_payload(_workspace(tmp_path), "ls")).encode())
            process.stdin.flush()
            process.wait(timeout=30)
            elapsed = time.monotonic() - started
            stdout = process.stdout.read()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
        assert elapsed < _timing_bound(2)
        assert "outcome timeout" in json.loads(stdout)["agent_message"]
        assert not _probe_ran(tmp_path)

    @pytest.mark.timing
    def test_the_bound_ends_a_call_that_a_child_in_its_own_session_holds_open(self, tmp_path: Path, gnu_wrapper: str) -> None:
        pid_file = tmp_path / "child.pid"
        holder = f'import os, time\\nos.setsid()\\nopen("{pid_file}", "w").write(str(os.getpid()))\\ntime.sleep(40)'
        body = (
            f"'{sys.executable}' -c \"$(printf '{holder}')\" &\n"
            f"while [ ! -s '{pid_file}' ]; do sleep 0.05; done\n{HOOK_STARTED_TOUCH}printf '{{}}'\n"
        )
        try:
            output, elapsed, _ = _timed_run(tmp_path, body, deadline=2)
        finally:
            if pid_file.exists():
                with contextlib.suppress(ProcessLookupError):
                    os.kill(int(pid_file.read_text()), signal.SIGKILL)
        _assert_hook_started(tmp_path)
        assert "outcome timeout" in output["agent_message"]
        assert elapsed < _timing_bound(2)

    @pytest.mark.timing
    def test_the_bound_ends_a_call_that_a_child_under_gnu_timeout_holds_stderr_open(
        self, tmp_path: Path, gnu_wrapper: str
    ) -> None:
        sleep_argument = f"41.{os.getpid()}"
        body = f"{HOOK_STARTED_TOUCH}'{gnu_wrapper}' 40 sleep {sleep_argument} >/dev/null &\nprintf '{{}}'\n"
        try:
            output, elapsed, _ = _timed_run(tmp_path, body, deadline=2)
        finally:
            subprocess.run(["pkill", "-f", f"sleep {sleep_argument}"], check=False)
        _assert_hook_started(tmp_path)
        assert "outcome timeout" in output["agent_message"]
        assert elapsed < _timing_bound(2)

    @pytest.mark.timing
    def test_a_hook_that_closes_stdout_and_stderr_and_sleeps_past_the_deadline_takes_the_timeout_outcome(
        self, tmp_path: Path, gnu_wrapper: str
    ) -> None:
        output, elapsed, _ = _timed_run(tmp_path, f"{HOOK_STARTED_TOUCH}exec >&- 2>&-\nsleep 30\n", deadline=2)
        _assert_hook_started(tmp_path)
        assert "outcome timeout" in output["agent_message"]
        assert elapsed < _timing_bound(2)

    @pytest.mark.timing
    def test_a_hook_still_running_at_the_deadline_that_prints_an_allow_and_exits_zero_on_the_term_is_a_timeout(
        self, tmp_path: Path, gnu_wrapper: str
    ) -> None:
        body = f"trap 'printf \"{{}}\"; exit 0' TERM\n{HOOK_STARTED_TOUCH}sleep 30 &\nwait $!\n"
        output, elapsed, env = _timed_run(tmp_path, body, deadline=2)
        _assert_hook_started(tmp_path)
        assert output["permission"] == "deny"
        assert "outcome timeout" in output["agent_message"]
        assert elapsed < _timing_bound(2)
        assert read_log_lines(env)[-1]["out"] == "timeout"

    # The child is its own `sh -c` process, so the `$$` it writes is its own pid. It writes a result after a sleep that
    # outlasts the deadline and the grace period.
    CHILD_SLEEP_SECONDS = 6
    CHILD_COMMAND = 'echo $$ > "$1/started"; sleep 6; echo done > "$1/result"'

    @staticmethod
    def _process_ended(pid: int) -> bool:
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False).stdout.strip()
        return state == "" or state.startswith("Z")

    def _child_body(self, tmp_path: Path, trap: str) -> str:
        return f"{trap}\nsh -c '{self.CHILD_COMMAND}' _ '{tmp_path}' &\nwait\n"

    @pytest.mark.timing
    @pytest.mark.parametrize("trap", ["trap 'exit 0' TERM", "trap '' TERM"], ids=["exits-zero-on-term", "ignores-term"])
    def test_the_deadline_ends_the_hooks_own_child_in_its_group(self, tmp_path: Path, gnu_wrapper: str, trap: str) -> None:
        output, _, _ = _timed_run(tmp_path, self._child_body(tmp_path, trap), deadline=2)
        assert "outcome timeout" in output["agent_message"]
        marker = tmp_path / "started"
        assert marker.exists(), "the child never started"
        pid = int(marker.read_text())
        give_up = time.monotonic() + self.CHILD_SLEEP_SECONDS + 2
        while not self._process_ended(pid) and time.monotonic() < give_up:
            time.sleep(0.1)
        assert self._process_ended(pid), "the hook's child outlived the wrapper's signals"
        assert not (tmp_path / "result").exists()

    @pytest.mark.timing
    def test_the_control_run_with_the_deadline_above_the_sleep_writes_the_result(self, tmp_path: Path, gnu_wrapper: str) -> None:
        output, _, _ = _timed_run(tmp_path, self._child_body(tmp_path, "trap 'exit 0' TERM"), deadline=20)
        assert output == {}
        assert (tmp_path / "result").exists()

    def test_output_at_the_cap_passes_on_stderr_and_one_byte_past_it_takes_the_cap_deny_on_each_stream(
        self, tmp_path: Path, gnu_wrapper: str
    ) -> None:
        at_cap, _, _ = _timed_run(tmp_path, f"head -c {OUTPUT_CAP} /dev/zero | tr '\\0' ' ' >&2\n", deadline=10)
        assert at_cap == {}
        for number, stream in ((1, "stdout"), (2, "stderr")):
            run_dir = tmp_path / stream
            run_dir.mkdir()
            past, _, _ = _timed_run(run_dir, f"head -c {OUTPUT_CAP + 1} /dev/zero | tr '\\0' ' ' >&{number}\n", deadline=10)
            assert "outcome hook-output-cap" in past["agent_message"], stream
            assert stream in past["agent_message"]

    # --- peak memory under endless output ---
    # The shim runs inside the audit wrapper, which reads its own peak resident size (VmHWM in /proc/self/status) before it exits.
    # /proc exists only on Linux, so only Linux CI can run these rows.

    ENDLESS_STDOUT = "yes x\n"
    ENDLESS_CHILD_STDERR = "'{wrapper}' 30 yes x >&2\n"

    def _peak_kib(self, tmp_path: Path, body: str, *, raised_cap: bool = False) -> tuple[int, str]:
        env = make_shim_env(tmp_path)
        script = write_hook_script(tmp_path / "hook.sh", body)
        args = hook_args("PreToolUse", script=script, matcher="Bash", deadline=20)
        payload = json.dumps(shell_pre_payload(_workspace(tmp_path), "ls")).encode()
        if raised_cap:
            result, _, extra = run_audited(
                [],
                mode="call",
                function="run",
                arguments=(args,),
                overrides={"HOOK_OUTPUT_CAP_BYTES": 64 * 1024 * 1024},
                events=(),
                payload=payload,
                env=shim_process_env(env),
                cwd=_scratch(tmp_path),
            )
        else:
            result, _, extra = run_audited(args, events=(), payload=payload, env=shim_process_env(env), cwd=_scratch(tmp_path))
        assert result.returncode == 0, result.stderr
        return extra["vm_hwm_kib"], result.stdout.decode()

    @pytest.mark.skipif(platform.system() != "Linux", reason="absent: VmHWM is read from /proc, which only Linux has")
    @pytest.mark.parametrize("writer", ["stdout", "child-stderr"])
    def test_endless_output_keeps_the_shims_peak_memory_within_sixteen_mebibytes_of_a_no_output_control(
        self, tmp_path: Path, gnu_wrapper: str, writer: str
    ) -> None:
        (tmp_path / "control").mkdir()
        (tmp_path / "endless").mkdir()
        control, _ = self._peak_kib(tmp_path / "control", "cat > /dev/null\n")
        body = self.ENDLESS_STDOUT if writer == "stdout" else self.ENDLESS_CHILD_STDERR.format(wrapper=gnu_wrapper)
        endless, shim_output = self._peak_kib(tmp_path / "endless", body)
        # The shim must have denied at the cap, so a hook that never started cannot read as a green memory bound.
        assert "hook-output-cap" in shim_output
        assert endless - control <= 16 * 1024

    @pytest.mark.skipif(platform.system() != "Linux", reason="absent: VmHWM is read from /proc, which only Linux has")
    def test_the_seeded_control_with_the_cap_raised_to_sixty_four_mebibytes_exceeds_the_bound(
        self, tmp_path: Path, gnu_wrapper: str
    ) -> None:
        (tmp_path / "control").mkdir()
        (tmp_path / "endless").mkdir()
        control, _ = self._peak_kib(tmp_path / "control", "cat > /dev/null\n")
        endless, _ = self._peak_kib(tmp_path / "endless", self.ENDLESS_STDOUT, raised_cap=True)
        assert endless - control > 16 * 1024

    # --- unit rows, in-process, with a recording stand-in for Popen ---

    def test_the_argv_is_the_recorded_wrapper_the_grace_the_time_left_and_the_script_with_no_foreground(
        self, monkeypatch: pytest.MonkeyPatch, stand_in
    ) -> None:
        made, _ = stand_in
        monkeypatch.setattr(SHIM_MODULE, "time", _Clock(0.0))
        _call_run_wrapped(deadline_at=8.0)
        assert made[0].argv == ["/recorded/timeout", "-k", "2", "8.000", "/hook.sh"]
        assert "--foreground" not in made[0].argv

    @pytest.mark.parametrize(
        ("deadline_at", "clock_now"), [(0.0, 0.0), (0.0, 5.0), (0.0999, 0.0)], ids=["zero", "negative", "just-below-the-floor"]
    )
    def test_a_time_left_below_the_floor_takes_the_timeout_outcome_and_starts_no_hook(
        self, monkeypatch: pytest.MonkeyPatch, stand_in, deadline_at: float, clock_now: float
    ) -> None:
        made, _ = stand_in
        monkeypatch.setattr(SHIM_MODULE, "time", _Clock(clock_now))
        with pytest.raises(SHIM_MODULE.Refusal) as caught:
            _call_run_wrapped(deadline_at=deadline_at)
        assert caught.value.outcome == "timeout"
        assert made == []

    def test_a_time_left_at_the_floor_reaches_the_argv_as_one_hundred_milliseconds(
        self, monkeypatch: pytest.MonkeyPatch, stand_in
    ) -> None:
        made, _ = stand_in
        monkeypatch.setattr(SHIM_MODULE, "time", _Clock(0.0))
        _call_run_wrapped(deadline_at=0.1)
        assert made[0].argv[3] == "0.100"

    @pytest.mark.parametrize(
        ("returncode", "at_deadline", "expected"),
        [
            (124, True, ("refusal", "timeout")),
            (137, True, ("refusal", "timeout")),
            (-9, True, ("refusal", "timeout")),
            (125, True, ("refusal", "start-failed")),
            (143, True, ("refusal", "signal")),
            (-15, True, ("refusal", "signal")),
            (0, True, ("status", 0)),
            (1, True, ("status", 1)),
            (2, True, ("status", 2)),
            (126, True, ("status", 126)),
            (127, True, ("status", 127)),
            (124, False, ("status", 124)),
            (137, False, ("refusal", "signal")),
            (-9, False, ("refusal", "signal")),
            (-40, False, ("refusal", "signal")),
        ],
    )
    def test_the_wrappers_status_maps_by_whether_the_clock_has_reached_the_deadline(
        self, monkeypatch: pytest.MonkeyPatch, stand_in, returncode: int, at_deadline: bool, expected: tuple
    ) -> None:
        made, options = stand_in
        clock = _Clock(0.0)
        monkeypatch.setattr(SHIM_MODULE, "time", clock)
        options.update(returncode=returncode, on_wait=lambda: setattr(clock, "values", [8.0 if at_deadline else 1.0]))
        try:
            outcome = ("status", _call_run_wrapped(deadline_at=8.0)[0])
        except SHIM_MODULE.Refusal as refusal:
            outcome = ("refusal", refusal.outcome)
            if returncode == 125:
                assert "status:125" in refusal.detail
            if returncode == -40:
                assert "signal:40" in refusal.detail
        assert outcome == expected

    @pytest.mark.parametrize("arm", ["read-loop", "wait"])
    def test_the_shims_bound_takes_the_timeout_outcome_with_no_signal_no_reap_and_no_wait_without_a_timeout(
        self, monkeypatch: pytest.MonkeyPatch, stand_in, arm: str
    ) -> None:
        made, options = stand_in
        recorded: list[str] = []
        for name in ("kill", "killpg", "waitpid", "wait4", "waitid"):
            monkeypatch.setattr(SHIM_MODULE.os, name, lambda *args, _name=name, **kwargs: recorded.append(_name), raising=False)
        if arm == "read-loop":
            options.update(hold_open=True)
            monkeypatch.setattr(SHIM_MODULE, "time", _Clock(0.0, 100.0))
        else:
            options.update(wait_raises=True)
            monkeypatch.setattr(SHIM_MODULE, "time", _Clock(0.0))
        with pytest.raises(SHIM_MODULE.Refusal) as caught:
            _call_run_wrapped(deadline_at=3.0)
        assert caught.value.outcome == "timeout"
        process_calls = [call[0] for call in made[0].calls]
        assert process_calls == (["wait"] if arm == "wait" else [])
        if arm == "wait":
            assert made[0].calls[0][1] is not None
        assert recorded == []

    @pytest.mark.parametrize(
        ("symbol", "invites_retry"),
        [("EAGAIN", True), ("EMFILE", True), ("ENFILE", True), ("ENOMEM", True), ("EACCES", False)],
    )
    def test_a_popen_start_error_is_start_failed_and_invites_a_retry_only_for_a_transient_cause(
        self, monkeypatch: pytest.MonkeyPatch, stand_in, symbol: str, invites_retry: bool
    ) -> None:
        _, options = stand_in
        monkeypatch.setattr(SHIM_MODULE, "time", _Clock(0.0))
        number = getattr(errno, symbol)
        options["raises"] = (PermissionError if symbol == "EACCES" else OSError)(number, "CANARY-os-message", "/recorded/timeout")
        with pytest.raises(SHIM_MODULE.Refusal) as caught:
            _call_run_wrapped()
        assert caught.value.outcome == "start-failed"
        assert symbol in caught.value.detail
        assert "CANARY" not in caught.value.detail
        assert ("retry this call once" in caught.value.detail) is invites_retry
        assert ("Do not retry this call." in caught.value.detail) is not invites_retry

    def test_a_file_not_found_naming_the_recorded_wrapper_is_start_failed_never_the_unenterable_directory(
        self, monkeypatch: pytest.MonkeyPatch, stand_in
    ) -> None:
        _, options = stand_in
        monkeypatch.setattr(SHIM_MODULE, "time", _Clock(0.0))
        options["raises"] = FileNotFoundError(2, "No such file", "/recorded/timeout")
        with pytest.raises(SHIM_MODULE.Refusal) as caught:
            _call_run_wrapped(directory="/work")
        assert caught.value.outcome == "start-failed"
        assert "Do not retry this call." in caught.value.detail

    def test_a_start_error_naming_the_hooks_directory_is_the_unenterable_directory_outcome(
        self, monkeypatch: pytest.MonkeyPatch, stand_in
    ) -> None:
        _, options = stand_in
        monkeypatch.setattr(SHIM_MODULE, "time", _Clock(0.0))
        options["raises"] = FileNotFoundError(2, "No such file", "/work")
        with pytest.raises(SHIM_MODULE.Refusal) as caught:
            _call_run_wrapped(directory="/work")
        assert caught.value.outcome == "unenterable-dir"
        assert "/work" not in caught.value.detail

    @pytest.mark.parametrize("raised", ["value-error", "oserror-without-symbol"])
    def test_a_stat_of_the_script_that_raises_is_start_failed_naming_the_class_or_the_number(
        self, monkeypatch: pytest.MonkeyPatch, raised: str
    ) -> None:
        real_stat = os.stat

        def failing_stat(path, *args, **kwargs):
            if str(path) == "/the/hook.sh":
                raise ValueError("CANARY-message") if raised == "value-error" else OSError(99999, "CANARY-message")
            return real_stat(path, *args, **kwargs)

        monkeypatch.setattr(SHIM_MODULE.os, "stat", failing_stat)
        with pytest.raises(SHIM_MODULE.Refusal) as caught:
            SHIM_MODULE.check_script("/the/hook.sh")
        assert caught.value.outcome == "start-failed"
        assert ("ValueError" if raised == "value-error" else "errno:99999") in caught.value.detail
        assert "CANARY" not in caught.value.detail
