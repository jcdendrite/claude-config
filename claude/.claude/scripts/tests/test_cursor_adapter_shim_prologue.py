"""hook-shim.py: the prologue, interpreter isolation and the tool-path rule's two conditions."""

from __future__ import annotations

import ast
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from helpers import CLAUDE_DIR

from ._cursor_shim_helpers import (
    SHIM_MODULE,
    _scratch,
    _workspace,
)

sys.path.insert(0, str(CLAUDE_DIR))
from hooks.tests._cursor_payloads import (  # noqa: E402 (the import needs the sys.path entry above)
    CONVERSATION_ID,
    IS_ROOT,
    PARENT_CONVERSATION_ID,
    SHIM,
    SHIM_INTERPRETER_FLAGS,
    build_spec,
    hook_args,
    make_shim_env,
    recorded_tool_paths,
    recording_probe_script,
    run_shim,
    shell_pre_payload,
    shim_argv,
    shim_json,
    shim_process_env,
    symlink_bin_dir,
    wrapper_name,
    write_hook_script,
)

# The PATH the shim fixes after the install record, behind the recorded tool directories.
SYSTEM_PATH = ":".join(SHIM_MODULE.SYSTEM_PATH_DIRECTORIES)


# --- prologue ---


class TestShimPrologue:
    def test_a_jq_absent_from_the_inherited_path_changes_no_shim_outcome(self, tmp_path: Path) -> None:
        env = make_shim_env(tmp_path)
        bin_dir = symlink_bin_dir(tmp_path, "bash", "dirname")
        root = _workspace(tmp_path)
        script = recording_probe_script(tmp_path / "hook.sh", tmp_path / "saved.json")
        result = run_shim(
            env,
            shell_pre_payload(root, "ls"),
            hook_args("PreToolUse", script=script),
            cwd=_scratch(tmp_path),
            extra_env={"PATH": str(bin_dir)},
        )
        assert shim_json(result) == {}
        assert (tmp_path / "saved.json").exists()

    @pytest.mark.skipif(IS_ROOT, reason="root reads a mode-000 file")
    def test_an_unreadable_record_takes_the_install_record_outcome(self, tmp_path: Path) -> None:
        env = make_shim_env(tmp_path)
        env.record_path.chmod(0)
        root = _workspace(tmp_path)
        output = shim_json(
            run_shim(
                env, shell_pre_payload(root, "ls"), hook_args("PreToolUse", script=tmp_path / "x.sh"), cwd=_scratch(tmp_path)
            )
        )
        assert "install-record" in output["agent_message"]

    def test_the_record_is_read_afresh_under_each_home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        seen = []
        for name in ("one", "two"):
            home_dir = tmp_path / name
            home_dir.mkdir()
            env = make_shim_env(home_dir)
            monkeypatch.setenv("HOME", str(env.home))
            seen.append(SHIM_MODULE.read_install_record(SHIM_MODULE.CallState())["routed_config_dir"])
        assert seen == [str(tmp_path / "one" / "config"), str(tmp_path / "two" / "config")]

    def test_the_path_the_hook_sees_is_the_recorded_tool_directories_then_the_system_ones(self, tmp_path: Path) -> None:
        paths = recorded_tool_paths()
        env = make_shim_env(
            tmp_path, tool_paths={"jq": paths["jq"], "bash": paths["bash"], "timeout": paths[wrapper_name()]}
        )
        root = _workspace(tmp_path)
        script = write_hook_script(tmp_path / "hook.sh", f"printf '%s' \"$PATH\" > '{tmp_path / 'path'}'\n")
        run_shim(env, shell_pre_payload(root, "ls"), hook_args("PreToolUse", script=script), cwd=_scratch(tmp_path))
        directories = []
        for tool in ("jq", "bash", "timeout"):
            directory = str(Path(paths[wrapper_name() if tool == "timeout" else tool]).parent)
            if directory not in directories:
                directories.append(directory)
        assert (tmp_path / "path").read_text() == ":".join([*directories, *SYSTEM_PATH.split(":")])

    def test_two_recorded_tools_in_one_directory_put_that_directory_on_the_path_once(self, tmp_path: Path) -> None:
        paths = recorded_tool_paths()
        extra_directory = tmp_path / "extra-bin"
        recorded = {
            "jq": paths["jq"],
            "extra-one": str(extra_directory / "one"),
            "extra-two": str(extra_directory / "two"),
            "timeout": paths[wrapper_name()],
        }
        env = make_shim_env(tmp_path, tool_paths=recorded)
        script = write_hook_script(tmp_path / "hook.sh", f"printf '%s' \"$PATH\" > '{tmp_path / 'path'}'\n")
        run_shim(
            env, shell_pre_payload(_workspace(tmp_path), "ls"), hook_args("PreToolUse", script=script), cwd=_scratch(tmp_path)
        )
        directories = []
        for tool_path in recorded.values():
            directory = str(Path(tool_path).parent)
            if directory not in directories:
                directories.append(directory)
        assert (tmp_path / "path").read_text() == ":".join([*directories, *SYSTEM_PATH.split(":")])
        assert (tmp_path / "path").read_text().split(":").count(str(extra_directory)) == 1

    def test_the_hook_runs_with_the_routed_config_dir_and_without_the_claude_process_markers(self, tmp_path: Path) -> None:
        env = make_shim_env(tmp_path)
        root = _workspace(tmp_path)
        script = write_hook_script(
            tmp_path / "hook.sh",
            "{ printf 'dir=%s\\n' \"$CLAUDE_CONFIG_DIR\"; "
            "env | grep -c -E '^(CLAUDECODE|CLAUDE_CODE_ENTRYPOINT|CLAUDE_PID)=' ; } > '" + str(tmp_path / "seen") + "'\n",
        )
        run_shim(
            env,
            shell_pre_payload(root, "ls"),
            hook_args("PreToolUse", script=script),
            cwd=_scratch(tmp_path),
            extra_env={
                "CLAUDECODE": "1",
                "CLAUDE_CODE_ENTRYPOINT": "cli",
                "CLAUDE_PID": "123",
                "CLAUDE_CONFIG_DIR": "/elsewhere",
            },
        )
        assert (tmp_path / "seen").read_text().splitlines() == [f"dir={env.config_dir}", "0"]

    def test_a_host_pid_the_shim_inherited_from_an_ide_shell_call_never_reaches_the_hook(self, tmp_path: Path) -> None:
        env = make_shim_env(tmp_path)
        script = write_hook_script(
            tmp_path / "hook.sh", f"printf '%s' \"${{CLAUDE_CONFIG_ADAPTER_HOST_PID-unset}}\" > '{tmp_path / 'seen'}'\n"
        )
        run_shim(
            env,
            shell_pre_payload(_workspace(tmp_path), "ls"),
            hook_args("PreToolUse", script=script),
            cwd=_scratch(tmp_path),
            extra_env={"CLAUDE_CONFIG_ADAPTER_HOST_PID": "9999"},
        )
        assert (tmp_path / "seen").read_text() == "unset"

    @pytest.mark.parametrize(
        ("spec_id", "expected_session_id"),
        [("shell_pre", CONVERSATION_ID), ("shell_pre_child", PARENT_CONVERSATION_ID)],
        ids=["root-call", "subagent-call"],
    )
    def test_the_hook_runs_with_the_session_id_the_shim_resolved_which_is_the_parents_for_a_subagent_call(
        self, tmp_path: Path, spec_id: str, expected_session_id: str
    ) -> None:
        env = make_shim_env(tmp_path)
        script = write_hook_script(
            tmp_path / "hook.sh", f"printf '%s' \"${{CLAUDE_CONFIG_ADAPTER_SESSION_ID-unset}}\" > '{tmp_path / 'seen'}'\n"
        )
        run_shim(
            env,
            build_spec(spec_id, _workspace(tmp_path)).cursor,
            hook_args("PreToolUse", script=script),
            cwd=_scratch(tmp_path),
        )
        assert (tmp_path / "seen").read_text() == expected_session_id

    @pytest.mark.timing
    def test_a_kill_of_the_shim_mid_call_leaves_the_wrapper_bounding_the_hook(self, tmp_path: Path) -> None:
        env = make_shim_env(tmp_path)
        root = _workspace(tmp_path)
        started = tmp_path / "started"
        # The sleep argument is unique to this run, so pgrep sees the hook's child as well as the script.
        sleep_argument = f"60.{os.getpid()}"
        script = write_hook_script(tmp_path / "hook.sh", f"trap '' TERM\ntouch '{started}'\nsleep {sleep_argument}\n")
        deadline = 2
        process = subprocess.Popen(
            shim_argv(*hook_args("PreToolUse", script=script, deadline=deadline)),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=_scratch(tmp_path),
            env=shim_process_env(env),
        )
        process.stdin.write(json.dumps(shell_pre_payload(root, "ls")).encode())
        process.stdin.close()
        give_up = time.monotonic() + 15
        while not started.exists() and time.monotonic() < give_up:
            time.sleep(0.05)
        assert started.exists()
        # Positive control: the same match sees the live sleep before the kill, so an empty match afterwards means it ended.
        give_up = time.monotonic() + 15
        while time.monotonic() < give_up:
            running = subprocess.run(["pgrep", "-f", f"sleep {sleep_argument}"], capture_output=True, text=True, check=False)
            if running.stdout.strip() != "":
                break
            time.sleep(0.05)
        assert running.stdout.strip() != "", "the match does not see the hook's sleep while it runs"
        process.send_signal(signal.SIGKILL)
        process.wait(timeout=15)
        give_up = time.monotonic() + SHIM_MODULE.worst_case_wall_seconds(deadline) + SHIM_MODULE.MARGIN_SECONDS
        leftover = ""
        while time.monotonic() < give_up:
            leftover = subprocess.run(
                ["pgrep", "-f", f"sleep {sleep_argument}"], capture_output=True, text=True, check=False
            ).stdout
            if leftover.strip() == "":
                break
            time.sleep(0.1)
        assert leftover.strip() == "", "the hook's sleep outlived the wrapper's deadline after the shim was killed"

    @staticmethod
    def _path_names(*roots: Path) -> set[str]:
        names = set()
        for root in roots:
            for directory, subdirectories, files in os.walk(root):
                names.update(os.path.join(directory, name) for name in (*subdirectories, *files))
        return names

    @pytest.mark.parametrize("payload_field", ["content", "tool_output", "ids-and-names"])
    def test_no_payload_or_hook_output_byte_reaches_a_path_name(self, tmp_path: Path, payload_field: str) -> None:
        """A canary in a payload of at least 1 MiB is at no path name while the hook runs, nor after a SIGKILL to the shim.

        After a normal end the names under the home, the config dir and TMPDIR add only the log, the first-call directory and
        the first-call mark."""
        canary = "CANARY-5f3a9c-do-not-persist"
        root = _workspace(tmp_path)
        padding = "x" * (1024 * 1024)
        session_id = CONVERSATION_ID
        if payload_field == "content":
            payload = build_spec("read_before_file", root).cursor
            payload["content"] = f"{padding}{canary}{padding}"
            claude_event = "PreToolUse"
        elif payload_field == "tool_output":
            payload = build_spec("shell_post", root).cursor
            payload["tool_output"] = json.dumps({"stdout": f"{padding}{canary}{padding}", "exitCode": 0})
            claude_event = "PostToolUse"
        else:
            # The parent id is the call's session id and so names the first-call mark by design. Only this row puts a canary in
            # an id field, and every other field is no path name.
            session_id = f"{canary}-parent"
            payload = shell_pre_payload(root, f"echo {canary}", description=canary)
            payload.update(
                tool_name=f"MCP:{canary}",
                tool_input={"file_path": str(root / "src" / f"{canary}.txt"), "note": f"{padding}{canary}"},
                conversation_id=f"{canary}-conversation",
                parent_agent_id=session_id,
                generation_id=f"{canary}-generation",
                tool_use_id=f"{canary}-tool-use",
            )
            claude_event = "PreToolUse"
        for kill_the_shim in (False, True):
            run_dir = tmp_path / f"run-{kill_the_shim}"
            run_dir.mkdir()
            env = make_shim_env(run_dir)
            ready, go, kind = run_dir / "ready", run_dir / "go", run_dir / "stdin-kind"
            script = write_hook_script(
                run_dir / "hook.sh",
                f"[ -p /dev/stdin ] && echo pipe > '{kind}' || echo other > '{kind}'\n"
                f"cat > /dev/null\nprintf '{canary}'\nprintf '{canary}' >&2\ntouch '{ready}'\n"
                f"for _ in $(seq 1 600); do [ -e '{go}' ] && break; sleep 0.05; done\n",
            )
            tmp_dir = run_dir / "tmp"
            tmp_dir.mkdir()
            watched = (env.home, env.config_dir, tmp_dir)
            before = self._path_names(*watched)
            process = subprocess.Popen(
                shim_argv(*hook_args(claude_event, script=script, deadline=60)),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=_scratch(run_dir),
                env=shim_process_env(env, {"TMPDIR": str(tmp_dir)}),
            )
            process.stdin.write(json.dumps(payload).encode())
            process.stdin.close()
            deadline = time.monotonic() + 30
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert ready.exists(), "the hook never reached its wait"
            if kill_the_shim:
                process.kill()
                process.wait(timeout=15)
            hits = subprocess.run(
                ["grep", "-rl", "--binary-files=text", canary, *map(str, watched)], capture_output=True, text=True, check=False
            )
            while_running = self._path_names(*watched)
            go.touch()
            if not kill_the_shim:
                process.communicate(timeout=30)
            after = self._path_names(*watched)
            assert hits.stdout == "", f"payload or hook output found in a file under the watched directories: {hits.stdout}"
            assert while_running == before, f"names appeared while the hook ran: {sorted(while_running - before)}"
            state = env.state_dir
            expected_after = (
                before
                if kill_the_shim
                else {
                    *before,
                    str(state / "shim.log"),
                    str(state / "first-call"),
                    str(state / "first-call" / session_id),
                }
            )
            assert after == expected_after, f"unexpected names: {sorted(after ^ expected_after)}"
            assert [name for name in after if canary in name] == (
                [str(state / "first-call" / session_id)] if (payload_field == "ids-and-names" and not kill_the_shim) else []
            )
            assert kind.read_text().strip() == "pipe"


# --- interpreter isolation ---


def _canary_writer(path: Path) -> str:
    return f"from pathlib import Path\nPath({str(path)!r}).write_text('ran')\n"


FLAG_PROBE = "import sys\nprint(sys.flags.isolated, sys.flags.no_site, 'site' in sys.modules)\n"


def _imported_non_standard_names(source: str) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names.add((node.module or "").split(".")[0])
    return names - set(sys.stdlib_module_names)


class TestShimInterpreterIsolation:
    def test_the_builders_flags_are_dash_i_then_dash_s(self) -> None:
        assert SHIM_INTERPRETER_FLAGS == ("-I", "-S")
        assert shim_argv("x")[1:3] == ["-I", "-S"]

    def test_the_probe_reports_isolated_no_site_and_site_absent_under_the_builders_flags(self, tmp_path: Path) -> None:
        probe = tmp_path / "probe.py"
        probe.write_text(FLAG_PROBE)
        result = subprocess.run(shim_argv(shim=probe), capture_output=True, text=True, check=False)
        assert result.stdout.split() == ["1", "1", "False"]

    def test_a_pythonpath_directory_holding_a_json_py_is_not_imported(self, tmp_path: Path) -> None:
        poison = tmp_path / "poison"
        poison.mkdir()
        canary = tmp_path / "pythonpath-canary"
        (poison / "json.py").write_text(_canary_writer(canary))
        probe = tmp_path / "probe.py"
        probe.write_text("import json\nprint(json.dumps(1))\n")
        env = {**os.environ, "PYTHONPATH": str(poison)}
        control = subprocess.run([sys.executable, str(probe)], capture_output=True, check=False, env=env)
        assert canary.exists(), f"the control run did not import the poisoned json: {control.stderr!r}"
        canary.unlink()
        flagged = subprocess.run(shim_argv(shim=probe), capture_output=True, check=False, env=env)
        assert flagged.returncode == 0
        assert not canary.exists()

    def test_a_json_py_beside_the_script_is_not_imported(self, tmp_path: Path) -> None:
        canary = tmp_path / "beside-canary"
        (tmp_path / "json.py").write_text(_canary_writer(canary))
        probe = tmp_path / "probe.py"
        probe.write_text("import json\nprint(json.dumps(1))\n")
        subprocess.run([sys.executable, str(probe)], capture_output=True, check=False)
        assert canary.exists(), "the control run did not import the neighbouring json"
        canary.unlink()
        flagged = subprocess.run(shim_argv(shim=probe), capture_output=True, check=False)
        assert flagged.returncode == 0
        assert not canary.exists()

    def _site_vector_run(self, tmp_path: Path, file_name: str, write_vector) -> tuple[Path, str]:
        """A scratch environment with one startup vector in its site directory. Returns its canary and interpreter."""
        environment_dir = tmp_path / "venv"
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(environment_dir)], check=True, capture_output=True)
        site_dir = next((environment_dir / "lib").glob("python*/site-packages"))
        canary = tmp_path / f"{file_name}-canary"
        write_vector(site_dir / file_name, canary)
        return canary, str(environment_dir / "bin" / "python")

    @staticmethod
    def _write_pth(path: Path, canary: Path) -> None:
        path.write_text(f"import pathlib; pathlib.Path({str(canary)!r}).write_text('ran')\n")

    @staticmethod
    def _write_sitecustomize(path: Path, canary: Path) -> None:
        path.write_text(_canary_writer(canary))

    @pytest.mark.parametrize(
        ("file_name", "writer"),
        [("evil.pth", "_write_pth"), ("sitecustomize.py", "_write_sitecustomize")],
        ids=["pth", "sitecustomize"],
    )
    def test_a_site_directory_startup_vector_runs_under_dash_i_alone_and_not_under_the_builders_flags(
        self, tmp_path: Path, file_name: str, writer: str
    ) -> None:
        canary, interpreter = self._site_vector_run(tmp_path, file_name, getattr(self, writer))
        probe = tmp_path / "probe.py"
        probe.write_text("print('ok')\n")
        subprocess.run([interpreter, "-I", str(probe)], capture_output=True, check=True)
        if not canary.exists():
            # Homebrew's interpreter ships a sitecustomize in its stdlib directory, which wins over the scratch one.
            pytest.skip(f"absent: this interpreter's control run (dash I alone) does not run a scratch {file_name}")
        canary.unlink()
        subprocess.run([interpreter, *SHIM_INTERPRETER_FLAGS, str(probe)], capture_output=True, check=True)
        assert not canary.exists()

    def test_pythonhome_set_to_an_empty_directory_stops_an_unflagged_start_and_not_a_flagged_one(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty-home"
        empty.mkdir()
        probe = tmp_path / "probe.py"
        probe.write_text("print('ok')\n")
        env = {**os.environ, "PYTHONHOME": str(empty)}
        control = subprocess.run([sys.executable, str(probe)], capture_output=True, check=False, env=env)
        assert control.returncode != 0
        flagged = subprocess.run(shim_argv(shim=probe), capture_output=True, text=True, check=False, env=env)
        assert (flagged.returncode, flagged.stdout.strip()) == (0, "ok")

    def test_the_shim_imports_only_standard_library_modules(self) -> None:
        assert _imported_non_standard_names(SHIM.read_text()) == set()

    def test_a_seeded_non_standard_import_is_found_by_the_scan(self) -> None:
        assert _imported_non_standard_names("import json\ndef f():\n    import requests\n") == {"requests"}
        assert _imported_non_standard_names("from yaml import safe_load\n") == {"yaml"}


# --- the tool-path rule's two conditions ---


class TestToolPathPredicates:
    """`is_valid_tool_path` is the conjunction of one predicate per condition, so `install` can name the first that fails."""

    @pytest.mark.parametrize(
        ("value", "absolute_without_parent", "no_list_character"),
        [
            ("/usr/bin/jq", True, True),
            ("bin/jq", False, True),
            ("/a/../b", False, True),
            ("/a..b/c", True, True),
            ("/a:b", True, False),
            ("/a\nb", True, False),
            ("/a\0b", True, False),
            ("/x/../y:z", False, False),
            ("", False, True),
            (5, False, False),
            (None, False, False),
        ],
        ids=["good", "relative", "dot-dot", "dots-in-a-name", "colon", "newline", "nul", "both-fail", "empty", "int", "none"],
    )
    def test_each_predicate_judges_only_its_own_condition(
        self, value: object, absolute_without_parent: bool, no_list_character: bool
    ) -> None:
        assert SHIM_MODULE.is_absolute_without_parent_component(value) is absolute_without_parent
        assert SHIM_MODULE.holds_no_path_list_character(value) is no_list_character
        assert SHIM_MODULE.is_valid_tool_path(value) is (absolute_without_parent and no_list_character)
