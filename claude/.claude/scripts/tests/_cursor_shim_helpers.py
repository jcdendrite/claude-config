"""The loaded hook-shim.py module and the helpers its test files share.

The translators are tested in test_cursor_adapter_translate.py. Every test that starts the shim runs it under a temporary
home that holds its install record, with the process directory outside any repository, as Cursor runs user hooks."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from helpers import CLAUDE_DIR

sys.path.insert(0, str(CLAUDE_DIR))
from hooks.tests._cursor_payloads import (  # noqa: E402 (the import needs the sys.path entry above)
    CONVERSATION_ID,
    SHIM,
    SHIM_INTERPRETER_FLAGS,
    STUBBED_WALK_DEADLINE_SECONDS,
    ShimEnv,
    build_spec,
    hook_args,
    load_shim,
    make_shim_env,
    recorded_tool_paths,
    recording_probe_script,
    run_shim,
    shell_pre_payload,
    shim_json,
    shim_process_env,
    wrapper_name,
    write_hook_script,
)

SHIM_MODULE = load_shim()
RECOVERY_SENTENCE = SHIM_MODULE.RECOVERY_SENTENCE
OUTPUT_CAP = SHIM_MODULE.HOOK_OUTPUT_CAP_BYTES
PAYLOAD_CAP = SHIM_MODULE.PAYLOAD_CAP_BYTES


def _scratch(tmp_path: Path) -> Path:
    """A directory outside any repository to run the shim from, as Cursor runs user hooks."""
    scratch = tmp_path / "cwd"
    scratch.mkdir(exist_ok=True)
    return scratch


def _workspace(tmp_path: Path) -> Path:
    root = (tmp_path / "workspace").resolve()
    (root / "src").mkdir(parents=True, exist_ok=True)
    return root


def _assert_deny_text_rules(output: dict) -> None:
    """Over every shim-authored deny: both fields equal, the Recovery sentence present, and no forbidden name."""
    text = output["agent_message"]
    assert output["user_message"] == text
    assert text.endswith(RECOVERY_SENTENCE)
    assert "claude-config repository" in text
    lowered = text.lower()
    assert "uninstall" not in lowered
    assert "cursor-adapter-state" not in text
    assert "shim.log" not in text


def _probe_ran(tmp_path: Path) -> bool:
    return (tmp_path / "saved.json").exists()


def _run_hook_body(
    tmp_path: Path, body: str, *, fail_closed: bool = True, deadline: int = 10, extra_env: dict | None = None
) -> tuple[dict, ShimEnv]:
    env = make_shim_env(tmp_path)
    root = _workspace(tmp_path)
    script = write_hook_script(tmp_path / "hook.sh", body)
    args = hook_args("PreToolUse", script=script, matcher="Bash", fail_closed=fail_closed, deadline=deadline)
    result = run_shim(env, shell_pre_payload(root, "echo hi"), args, cwd=_scratch(tmp_path), extra_env=extra_env)
    assert result.returncode == 0, result.stderr
    return shim_json(result), env


def _run_probe(
    tmp_path: Path,
    payload: dict | str | bytes,
    *,
    env: ShimEnv | None = None,
    fail_closed: bool = True,
    mode: str = "hook",
    **hook_kwargs,
) -> tuple[dict, ShimEnv]:
    """Run the shim with a probe hook that saves its stdin to saved.json, so a test can tell whether the hook ran."""
    env = env or make_shim_env(tmp_path)
    script = recording_probe_script(tmp_path / "hook.sh", tmp_path / "saved.json")
    event = hook_kwargs.pop("claude_event", "PreToolUse")
    args = hook_args(event, script=script, fail_closed=fail_closed, mode=mode, **hook_kwargs)
    result = run_shim(env, payload, args, cwd=_scratch(tmp_path))
    assert result.returncode == 0, result.stderr
    return shim_json(result), env


HOOK_STARTED_TOUCH = 'touch "${0%/*}/hook-started"\n'
HOOK_STARTED_THEN_SLEEP = HOOK_STARTED_TOUCH + "sleep 30\n"


def _assert_hook_started(tmp_path: Path) -> None:
    assert (tmp_path / "hook-started").exists(), "the hook never reached its marker line, so the path under test did not run"


SHIM_MODULE_EVENTS = ("subprocess.Popen", "os.exec", "os.fork", "os.posix_spawn", "os.spawn", "os.system")


def _recorded_wrapper() -> str:
    """The recorded GNU `timeout` or `gtimeout`. A host with none fails the hook-run rows, never skips them."""
    wrapper = recorded_tool_paths()[wrapper_name()]
    version = subprocess.run([wrapper, "--version"], capture_output=True, text=True, check=False).stdout
    assert "GNU coreutils" in version, f"{wrapper} is not GNU timeout"
    return wrapper


CANARY = "CANARY-fault-8c1d-never-in-output"
FAULT_PROGRAM = """
import json, sys
config = json.loads(sys.argv[1])
shim = config["shim"]
namespace = {"__name__": "cursor_hook_shim", "__file__": shim}
exec(compile(open(shim, "rb").read(), shim, "exec"), namespace)


class CanaryError(Exception):
    pass


def raiser(*arguments, **keywords):
    kind = config["raise"]
    if kind == "exception":
        raise CanaryError(config["canary"])
    if kind == "system-exit":
        raise SystemExit(config["canary"])
    if kind == "recursion":
        raise RecursionError(config["canary"])
    if kind == "oserror":
        raise OSError(5, config["canary"])
    if kind == "subprocess-error":
        raise sys.modules["subprocess"].SubprocessError(config["canary"])
    if kind == "refusal":
        raise namespace["Refusal"]("timeout", config["canary"])
    raise MemoryError(config["canary"])


if config.get("function"):
    namespace[config["function"]] = raiser
if config.get("short_write_then_raise"):
    real_write = sys.modules["os"].write
    seen = []

    def short_write(descriptor, data):
        if descriptor != 1:
            return real_write(descriptor, data)
        seen.append(1)
        if len(seen) == 1:
            return real_write(descriptor, bytes(data)[:3])
        raise OSError(5, config["canary"])

    sys.modules["os"].write = short_write
sys.exit(namespace["run"](config["argv"]))
"""


def _run_fault_program(
    tmp_path: Path, config: dict, payload: dict, args: list[str]
) -> tuple[subprocess.CompletedProcess, ShimEnv]:
    env = make_shim_env(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            *SHIM_INTERPRETER_FLAGS,
            "-c",
            FAULT_PROGRAM,
            json.dumps({"shim": str(SHIM), "argv": args, "canary": CANARY, **config}),
        ],
        input=json.dumps(payload).encode(),
        capture_output=True,
        cwd=_scratch(tmp_path),
        env=shim_process_env(env),
        check=False,
        timeout=60,
    )
    return result, env


def _candidate_shells() -> dict[str, str]:
    """Name to path for the candidate shells that exist here: the recorded bash, /bin/sh, zsh and dash."""
    found = {"bash": shutil.which("bash") or "bash"}
    for name, path in (("sh", "/bin/sh"), ("zsh", shutil.which("zsh")), ("dash", shutil.which("dash"))):
        if path and Path(path).exists():
            found[name] = path
    return found


# A project choice: the probe runs at collection, so a candidate shell whose startup file hangs must not hang the suite.
SHELL_PROBE_TIMEOUT_SECONDS = 10


CANDIDATE_SHELLS = _candidate_shells()


def _walkless_env(tmp_path: Path) -> ShimEnv:
    """An install record that names no bash, so `pin` mode starts no host walk and writes the unset form."""
    tool_paths = recorded_tool_paths()
    del tool_paths["bash"]
    return make_shim_env(tmp_path, tool_paths=tool_paths)


def _environment_line(env: ShimEnv, host_pid: str | None = None, session_id: str = CONVERSATION_ID) -> str:
    """The pin prefix's first line, spelled out here and not built by the shim, so a drift in its text fails a row."""
    exports = f"export CLAUDE_CONFIG_DIR='{env.config_dir}' CLAUDE_CONFIG_ADAPTER_SESSION_ID='{session_id}'"
    if host_pid is None:
        return f"unset CLAUDECODE CLAUDE_CONFIG_ADAPTER_HOST_PID; {exports}"
    return f"unset CLAUDECODE; {exports} CLAUDE_CONFIG_ADAPTER_HOST_PID={host_pid}"


def _pin_prefix(env: ShimEnv, directory: Path | str, host_pid: str | None = None, session_id: str = CONVERSATION_ID) -> str:
    """The whole pin prefix: the environment line, the pin line and the newline after each."""
    return f"{_environment_line(env, host_pid, session_id)}\nbuiltin cd -- '{directory}' || exit 1\n"


def _pin(env: ShimEnv, payload: dict, tmp_path: Path, *, fail_closed: bool = True) -> dict:
    args = hook_args("PreToolUse", mode="pin", fail_closed=fail_closed, deadline=STUBBED_WALK_DEADLINE_SECONDS)
    result = run_shim(env, payload, args, cwd=_scratch(tmp_path))
    assert result.returncode == 0, result.stderr
    return shim_json(result)


def _pin_check(env: ShimEnv, command: str, root: Path, tmp_path: Path) -> dict:
    payload = build_spec("shell_before_execution", root).cursor
    payload["command"] = command
    args = hook_args("PreToolUse", mode="pin-check")
    return shim_json(run_shim(env, payload, args, cwd=_scratch(tmp_path)))


def _prefixed(env: ShimEnv, tmp_path: Path, root: Path, command: str, **tool_input_extra) -> str:
    output = _pin(env, shell_pre_payload(root, command, **tool_input_extra), tmp_path)
    assert "updated_input" in output, output
    return output["updated_input"]["command"]
