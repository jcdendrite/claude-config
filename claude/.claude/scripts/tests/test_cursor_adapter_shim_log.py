"""hook-shim.py: the log and the process-start count."""

from __future__ import annotations

import errno
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest
from helpers import CLAUDE_DIR

from ._cursor_shim_helpers import (
    SHIM_MODULE,
    SHIM_MODULE_EVENTS,
    _pin_prefix,
    _probe_ran,
    _run_probe,
    _scratch,
    _walkless_env,
    _workspace,
)

sys.path.insert(0, str(CLAUDE_DIR))
from hooks.tests._cursor_payloads import (  # noqa: E402 (the import needs the sys.path entry above)
    CONTRACT_VERSION,
    CONVERSATION_ID,
    IS_ROOT,
    SHIM,
    SHIM_INTERPRETER_FLAGS,
    ShimEnv,
    build_spec,
    compile_shim_module,
    copy_adapter,
    file_tool_payload,
    hook_args,
    make_shim_env,
    read_log_lines,
    recording_probe_script,
    run_audited,
    run_shim,
    shell_pre_payload,
    shim_argv,
    shim_process_env,
    write_hook_script,
)

# --- the log ---

LOG_FIELD_NAMES = [name for name, _ in SHIM_MODULE.LOG_FIELDS]
LOG_WRITER_PROGRAM = (
    "import sys\n"
    "namespace = {'__name__': 'cursor_hook_shim', '__file__': sys.argv[1]}\n"
    "exec(compile(open(sys.argv[1], 'rb').read(), sys.argv[1], 'exec'), namespace)\n"
    "fields = {name: 'a' * maximum for name, maximum in namespace['LOG_FIELDS']}\n"
    "for _ in range(5):\n"
    "    namespace['write_log_line'](sys.argv[2], fields)\n"
)


class TestShimLogConcurrency:
    def test_the_field_maxima_with_their_separators_stay_under_the_line_cap(self) -> None:
        overflow = SHIM_MODULE.LOG_OVERFLOW_TOKEN
        longest = sum(len(name) + 1 + max(maximum, len(overflow)) for name, maximum in SHIM_MODULE.LOG_FIELDS)
        total = longest + (len(SHIM_MODULE.LOG_FIELDS) - 1) + 1
        assert total < SHIM_MODULE.LOG_LINE_MAX_BYTES
        assert re.fullmatch(SHIM_MODULE.LOG_VALUE_PATTERN, overflow)

    def test_the_table_holds_the_fault_the_py_and_the_run_mode_fields(self) -> None:
        names = LOG_FIELD_NAMES
        assert {"fault", "py", "runmode"} <= set(names)
        assert len(names) == len(set(names))

    def test_a_value_past_its_maximum_or_outside_the_grammar_becomes_the_overflow_token(self) -> None:
        line = SHIM_MODULE.format_log_line({"ts": "1", "reg": "a" * 65, "ev": "has-space!", "tool": "ok:tool", "out": "ok"})
        assert line == b"ts=1 reg=overflow ev=overflow tool=ok:tool out=ok\n"

    def test_a_field_passed_empty_is_omitted(self) -> None:
        assert SHIM_MODULE.format_log_line({"ts": "1", "reg": "", "out": "ok"}) == b"ts=1 out=ok\n"

    def test_forty_concurrent_writers_at_every_field_maximum_lose_and_tear_no_line(self, tmp_path: Path) -> None:
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        processes = [
            subprocess.Popen([sys.executable, *SHIM_INTERPRETER_FLAGS, "-c", LOG_WRITER_PROGRAM, str(SHIM), str(state_dir)])
            for _ in range(40)
        ]
        assert [process.wait(timeout=60) for process in processes] == [0] * 40
        lines = (state_dir / SHIM_MODULE.LOG_FILE_NAME).read_text().split("\n")
        assert lines.pop() == ""
        assert len(lines) == 200
        expected = " ".join(f"{name}={'a' * maximum}" for name, maximum in SHIM_MODULE.LOG_FIELDS)
        assert set(lines) == {expected}
        assert max(len(line.encode()) + 1 for line in lines) < SHIM_MODULE.LOG_LINE_MAX_BYTES

    def test_forty_concurrent_shims_each_write_one_whole_line(self, tmp_path: Path) -> None:
        env = make_shim_env(tmp_path)
        root = _workspace(tmp_path)
        args = hook_args("PreToolUse", mode="classify", label="L" * 64, fail_closed=False)
        payload = json.dumps(shell_pre_payload(root, "ls")).encode()
        processes = []
        try:
            for _ in range(40):
                process = subprocess.Popen(
                    shim_argv(*args),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=_scratch(tmp_path),
                    env=shim_process_env(env),
                )
                processes.append(process)
                # Delivered at spawn, so each shim's read deadline times only that shim.
                process.stdin.write(payload)
                process.stdin.close()
            for process in processes:
                process.communicate(timeout=120)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.wait()
        lines = env.log_path.read_text().splitlines()
        assert len(lines) == 40
        for line in lines:
            fields = dict(pair.split("=", 1) for pair in line.split(" "))
            assert fields["reg"] == "L" * 64
            assert fields["out"] == "classified"

    def _pin_call(self, env: ShimEnv, root: Path, scratch: Path, **payload_extra) -> subprocess.Popen:
        payload = shell_pre_payload(root, "ls")
        payload.update(payload_extra)
        process = subprocess.Popen(
            shim_argv(*hook_args("PreToolUse", mode="pin", fail_closed=False)),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=scratch,
            env=shim_process_env(env),
        )
        process.stdin.write(json.dumps(payload).encode())
        process.stdin.close()
        return process

    def test_forty_concurrent_first_calls_for_one_conversation_write_exactly_one_first_line(self, tmp_path: Path) -> None:
        env = make_shim_env(tmp_path)
        root, scratch = _workspace(tmp_path), _scratch(tmp_path)
        processes = [self._pin_call(env, root, scratch) for _ in range(40)]
        for process in processes:
            process.communicate(timeout=120)
        lines = read_log_lines(env)
        assert len(lines) == 40
        first_lines = [line for line in lines if "py" in line]
        assert len(first_lines) == 1
        assert first_lines[0]["py"] == f"{sys.version_info.major}.{sys.version_info.minor}"

    def test_a_later_call_of_the_conversation_writes_no_first_line_and_another_conversation_writes_its_own(
        self, tmp_path: Path
    ) -> None:
        env = make_shim_env(tmp_path)
        root, scratch = _workspace(tmp_path), _scratch(tmp_path)
        other_conversation = "conv-0000-4000-8000-aa"
        for extra in ({}, {}, {"conversation_id": other_conversation}):
            self._pin_call(env, root, scratch, **extra).communicate(timeout=60)
        assert ["py" in line for line in read_log_lines(env)] == [True, False, True]

    def test_a_childs_call_finds_its_parents_mark(self, tmp_path: Path) -> None:
        env = make_shim_env(tmp_path)
        root, scratch = _workspace(tmp_path), _scratch(tmp_path)
        self._pin_call(env, root, scratch).communicate(timeout=60)
        child = {"conversation_id": "conv-0000-4000-8000-bb", "parent_agent_id": CONVERSATION_ID}
        self._pin_call(env, root, scratch, **child).communicate(timeout=60)
        assert ["py" in line for line in read_log_lines(env)] == [True, False]

    def test_a_first_call_directory_that_is_a_link_gives_no_first_line_and_is_not_written_through(self, tmp_path: Path) -> None:
        env = make_shim_env(tmp_path)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (env.state_dir / "first-call").symlink_to(elsewhere)
        self._pin_call(env, _workspace(tmp_path), _scratch(tmp_path)).communicate(timeout=60)
        assert ["py" in line for line in read_log_lines(env)] == [False]
        assert list(elsewhere.iterdir()) == []

    def _logging_call(self, env: ShimEnv):
        call = SHIM_MODULE.CallState()
        call.started = time.monotonic()
        call.label, call.cursor_event, call.tool, call.outcome = "t", "preToolUse", "Shell", "ok"
        call.contract_version, call.state_dir, call.session_id = CONTRACT_VERSION, str(env.state_dir), CONVERSATION_ID
        return call

    def test_a_close_of_the_mark_that_raises_still_counts_the_call_as_first_and_escapes_nowhere(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = make_shim_env(tmp_path)
        real_open, real_close = os.open, os.close
        opened, closes = [], []

        def recording_open(*arguments, **keywords) -> int:
            descriptor = real_open(*arguments, **keywords)
            opened.append(descriptor)
            return descriptor

        def raising_first_close(descriptor: int) -> None:
            real_close(descriptor)
            closes.append(descriptor)
            if len(closes) == 1:
                raise OSError(errno.EIO, "CANARY-close")

        monkeypatch.setattr(SHIM_MODULE.os, "open", recording_open)
        monkeypatch.setattr(SHIM_MODULE.os, "close", raising_first_close)
        SHIM_MODULE.log_call(self._logging_call(env))
        # The mark is opened first, then the log, and the raising close is the mark's.
        assert len(opened) == len(closes) == 2
        assert closes[0] == opened[0]
        assert ["py" in line for line in read_log_lines(env)] == [True]

    def test_a_close_of_the_log_that_raises_after_a_complete_write_keeps_the_mark_and_escapes_nowhere(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = make_shim_env(tmp_path)
        real_close = os.close

        def raising_close(descriptor: int) -> None:
            real_close(descriptor)
            raise OSError(errno.EIO, "CANARY-close")

        monkeypatch.setattr(SHIM_MODULE.os, "close", raising_close)
        SHIM_MODULE.log_call(self._logging_call(env))
        monkeypatch.undo()
        assert ["py" in line for line in read_log_lines(env)] == [True]
        assert [path.name for path in (env.state_dir / "first-call").iterdir()] == [CONVERSATION_ID]

    def test_a_log_write_that_fails_after_the_mark_is_claimed_releases_the_mark_so_the_next_call_is_first(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = make_shim_env(tmp_path)

        def failing_write(state_dir: str, fields: dict) -> None:
            raise OSError(errno.ENOSPC, "CANARY-write")

        with monkeypatch.context() as patched:
            patched.setattr(SHIM_MODULE, "write_log_line", failing_write)
            SHIM_MODULE.log_call(self._logging_call(env))
        assert list((env.state_dir / "first-call").iterdir()) == []
        SHIM_MODULE.log_call(self._logging_call(env))
        assert ["py" in line for line in read_log_lines(env)] == [True]

    def test_every_log_line_is_name_value_pairs_from_the_grammar(self, tmp_path: Path) -> None:
        env = make_shim_env(tmp_path)
        root = _workspace(tmp_path)
        script = recording_probe_script(tmp_path / "hook.sh", tmp_path / "saved.json")
        run_shim(
            env, shell_pre_payload(root, "ls"), hook_args("PreToolUse", script=script, label="my-hook"), cwd=_scratch(tmp_path)
        )
        line = env.log_path.read_text().rstrip("\n")
        assert re.fullmatch(r"(?:[a-z]+=[0-9A-Za-z._:+-]+ )*[a-z]+=[0-9A-Za-z._:+-]+", line)
        fields = dict(pair.split("=", 1) for pair in line.split(" "))
        assert fields["reg"] == "my-hook"
        assert fields["ev"] == "preToolUse"
        assert fields["tool"] == "Shell"
        assert fields["ver"] == CONTRACT_VERSION
        assert fields["exit"] == "0"

    @pytest.mark.parametrize("kind", ["symlink", "read-only", "missing"])
    def test_a_state_dir_the_shim_cannot_use_leaves_no_log_line_and_changes_no_decision(self, tmp_path: Path, kind: str) -> None:
        if kind == "read-only" and IS_ROOT:
            pytest.skip("root writes into a read-only directory")
        env = make_shim_env(tmp_path)
        if kind == "symlink":
            env.state_dir.rmdir()
            elsewhere = tmp_path / "elsewhere"
            elsewhere.mkdir()
            env.state_dir.symlink_to(elsewhere)
        elif kind == "missing":
            env.state_dir.rmdir()
        else:
            env.state_dir.chmod(0o500)
        try:
            output, _ = _run_probe(tmp_path, shell_pre_payload(_workspace(tmp_path), "echo hi"), env=env, matcher="Bash")
        finally:
            if kind == "read-only":
                env.state_dir.chmod(0o700)
        assert output == {}
        assert _probe_ran(tmp_path)
        if kind == "symlink":
            assert list((tmp_path / "elsewhere").glob("*")) == []
        else:
            assert read_log_lines(env) == []


# --- process-start count ---

# The top-level modules a `hook`-mode call may have loaded when it ends, measured on Python 3.14 under the audit wrapper
# (whose `runpy.run_path` loads `runpy` and `pkgutil`). A new name here is a new import cost.
# The list depends on the interpreter's own imports: re-measure it by printing `loaded - ALLOWED_TOP_LEVEL_MODULES` from the
# row below under the new interpreter, and add only names the shim's imports or the interpreter's startup explain.
ALLOWED_TOP_LEVEL_MODULES = frozenset(
    {
        "abc", "builtins", "codecs", "collections", "contextlib", "copyreg", "encodings", "enum", "errno", "fcntl",
        "functools", "genericpath", "importlib", "io", "itertools", "json", "keyword", "linecache", "locale", "marshal",
        "math", "operator", "os", "pkgutil", "posix", "posixpath", "re", "reprlib", "runpy", "select", "selectors", "signal",
        "stat",
        "subprocess", "sys", "threading", "time", "types", "warnings", "weakref", "zipimport",
    }
)  # fmt: skip


# POSIX `os.spawn*` is `fork` then `exec` in os.py and raises no `os.spawn` event of its own, so that family has no seed.
SEEDED_STARTS = {
    "subprocess.Popen": "import subprocess\nsubprocess.Popen(['/usr/bin/true'])",
    "os.exec": "import os\nos.execv('/usr/bin/true', ['true'])",
    "os.fork": "import os\nos.fork()",
    "os.posix_spawn": "import os\nos.posix_spawn('/usr/bin/true', ['true'], {})",
    "os.system": "import os\nos.system('true')",
}


class TestShimExecCount:
    """Process starts per call are counted by an audit hook, so the count needs no time budget and cannot flake on wall time."""

    def _run(
        self, tmp_path: Path, payload: dict, args: list[str], *, env: ShimEnv | None = None
    ) -> tuple[subprocess.CompletedProcess, list[dict], dict]:
        env = env or make_shim_env(tmp_path)
        result, events, extra = run_audited(
            args, payload=json.dumps(payload).encode(), env=shim_process_env(env), cwd=_scratch(tmp_path)
        )
        assert result.returncode == 0, result.stderr
        return result, events, extra

    def test_a_hook_call_starts_exactly_one_process_the_wrapper(self, tmp_path: Path) -> None:
        script = write_hook_script(tmp_path / "hook.sh", "cat > /dev/null\n")
        _, events, _ = self._run(tmp_path, shell_pre_payload(_workspace(tmp_path), "ls"), hook_args("PreToolUse", script=script))
        assert [event["event"] for event in events] == ["subprocess.Popen"]

    @pytest.mark.parametrize("mode", ["classify", "pin-check"])
    def test_the_other_modes_start_no_process(self, tmp_path: Path, mode: str) -> None:
        root = _workspace(tmp_path)
        payload = build_spec("shell_before_execution", root).cursor if mode == "pin-check" else shell_pre_payload(root, "ls")
        _, events, _ = self._run(tmp_path, payload, hook_args("PreToolUse", mode=mode))
        assert events == []

    def test_pin_mode_starts_exactly_one_process_the_host_walk(self, tmp_path: Path) -> None:
        _, events, _ = self._run(tmp_path, shell_pre_payload(_workspace(tmp_path), "ls"), hook_args("PreToolUse", mode="pin"))
        assert [event["event"] for event in events] == ["subprocess.Popen"]

    def test_pin_mode_starts_no_process_over_a_record_that_names_no_bash(self, tmp_path: Path) -> None:
        _, events, _ = self._run(
            tmp_path,
            shell_pre_payload(_workspace(tmp_path), "ls"),
            hook_args("PreToolUse", mode="pin"),
            env=_walkless_env(tmp_path),
        )
        assert events == []

    @pytest.mark.parametrize("host_pid", ["4242", None], ids=["export-form", "unset-form"])
    def test_pin_check_starts_no_process_on_a_command_whose_prefix_carries_either_host_pid_form(
        self, tmp_path: Path, host_pid: str | None
    ) -> None:
        env = make_shim_env(tmp_path)
        root = _workspace(tmp_path)
        payload = build_spec("shell_before_execution", root).cursor
        payload["command"] = _pin_prefix(env, root, host_pid) + "ls"
        result, events, _ = self._run(tmp_path, payload, hook_args("PreToolUse", mode="pin-check"), env=env)
        assert events == []
        assert json.loads(result.stdout) == {}

    @pytest.mark.parametrize("mode", ["pin", "hook"])
    def test_a_leading_cd_into_a_directory_inside_a_root_starts_no_process_beyond_the_wrapper(
        self, tmp_path: Path, mode: str
    ) -> None:
        root = _workspace(tmp_path)
        env = _walkless_env(tmp_path)
        script = write_hook_script(tmp_path / "hook.sh", "cat > /dev/null\n")
        args = hook_args("PreToolUse", script=script) if mode == "hook" else hook_args("PreToolUse", mode=mode)
        result, events, _ = self._run(tmp_path, shell_pre_payload(root, f"cd {root / 'src'} && make"), args, env=env)
        assert [event["event"] for event in events] == (["subprocess.Popen"] if mode == "hook" else [])
        if mode == "pin":
            # A refusal also exits 0 with no process, and the first-root fallback pins `root`: the line must name the cd target.
            pinned = json.loads(result.stdout)["updated_input"]["command"]
            assert pinned.startswith(_pin_prefix(env, root / "src"))

    @pytest.mark.parametrize("tool", ["Read", "Write", "Delete"])
    def test_a_file_tool_call_resolving_an_agent_named_path_starts_no_process_beyond_the_wrapper(
        self, tmp_path: Path, tool: str
    ) -> None:
        # The target sits under the second root, so the first-root fallback and the file-tool branch start the hook in
        # different directories.
        first = _workspace(tmp_path)
        second = (tmp_path / "second").resolve()
        (second / "src").mkdir(parents=True)
        where = tmp_path / "where"
        script = write_hook_script(tmp_path / "hook.sh", f"pwd -P > '{where}'\ncat > /dev/null\n")
        payload = file_tool_payload(first, tool, str(second / "src" / "target.txt"))
        payload["workspace_roots"] = [str(first), str(second)]
        _, events, _ = self._run(tmp_path, payload, hook_args("PreToolUse", script=script))
        assert [event["event"] for event in events] == ["subprocess.Popen"]
        assert where.read_text().strip() == str(second)

    def test_a_call_the_matcher_does_not_select_starts_no_process(self, tmp_path: Path) -> None:
        script = write_hook_script(tmp_path / "hook.sh", "cat > /dev/null\n")
        _, events, _ = self._run(
            tmp_path, shell_pre_payload(_workspace(tmp_path), "ls"), hook_args("PreToolUse", script=script, matcher="Read")
        )
        assert events == []

    @pytest.mark.parametrize("family", list(SEEDED_STARTS))
    def test_a_seeded_start_of_each_event_family_counts_one(self, tmp_path: Path, family: str) -> None:
        scratch = tmp_path / "seeded.py"
        scratch.write_text(
            f"try:\n    {SEEDED_STARTS[family].replace(chr(10), chr(10) + '    ')}\nexcept RuntimeError:\n    pass\n"
        )
        result, events, _ = run_audited([], shim=scratch, block=tuple(SEEDED_STARTS))
        assert result.returncode == 0, result.stderr
        assert [event["event"] for event in events] == [family]

    def test_the_modules_loaded_after_a_hook_call_are_a_subset_of_the_allow_list(self, tmp_path: Path) -> None:
        script = write_hook_script(tmp_path / "hook.sh", "cat > /dev/null\n")
        _, _, extra = self._run(tmp_path, shell_pre_payload(_workspace(tmp_path), "ls"), hook_args("PreToolUse", script=script))
        loaded = {name for name in extra["modules"] if not name.startswith("_")}
        assert loaded <= ALLOWED_TOP_LEVEL_MODULES, sorted(loaded - ALLOWED_TOP_LEVEL_MODULES)

    def test_no_bytecode_file_appears_beside_a_scratch_copy_after_a_call_or_a_load(self, tmp_path: Path) -> None:
        copied = copy_adapter(tmp_path)
        env = make_shim_env(tmp_path)
        script = write_hook_script(tmp_path / "hook.sh", "cat > /dev/null\n")
        result = run_shim(
            env,
            shell_pre_payload(_workspace(tmp_path), "ls"),
            hook_args("PreToolUse", script=script),
            cwd=_scratch(tmp_path),
            shim=copied,
        )
        assert result.returncode == 0, result.stderr
        compile_shim_module(copied)
        assert list(copied.parent.rglob("__pycache__")) == []
        assert list(copied.parent.rglob("*.pyc")) == []

    def test_loading_the_module_without_running_main_opens_nothing_and_starts_nothing(self, tmp_path: Path) -> None:
        poisoned = {
            "PATH": "",
            "HOME": "/nonexistent-home",
            "PYTHONPATH": str(tmp_path / "poison"),
            "CLAUDE_CONFIG_DIR": "/nonexistent-config",
        }
        _, events, extra = run_audited([], mode="load", events=(*SHIM_MODULE_EVENTS, "open"), env=poisoned)
        assert events == []
        assert extra["descriptors_opened"] == 0
