"""Cursor payload builders and the shim harness, shared by the hooks and scripts test trees.

A non-test module, like _lib_sh_helpers.py. It holds:
- the builders for every Cursor payload the adapter tests send, each with its Claude-shaped twin;
- the one argv builder that starts hook-shim.py as the generated command does, and the one loader that loads it in-process;
- a harness that writes an install record under a temporary home and runs the shim against it.

Builders take the key paths and types from recorded Cursor payloads (shapes only) and inject
placeholder values. conversation_id, session_id, generation_id and the parent id are distinct values,
so a translation that reads the wrong id fails. A twin is a literal written beside its spec, never
computed from field-table.json.
"""

from __future__ import annotations

import contextlib
import copy
import functools
import json
import os
import re
import shutil
import subprocess
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path

from helpers import CLAUDE_DIR, HOOKS_DIR, REPO_ROOT

SETTINGS_JSON = CLAUDE_DIR / "settings.json"
ADAPTER_DIR = REPO_ROOT / "claude" / ".claude" / "scripts" / "cursor-adapter"
LIB_SH = HOOKS_DIR / "_lib.sh"
SHIM = ADAPTER_DIR / "hook-shim.py"
# The interpreter flags the generated command passes the shim, in order.
SHIM_INTERPRETER_FLAGS = ("-I", "-S")
# The one name every loader registers the shim under, because the file name holds a hyphen.
SHIM_MODULE_NAME = "cursor_hook_shim"
TOOL_MAP_JSON = ADAPTER_DIR / "tool-map.json"
FIELD_TABLE_JSON = ADAPTER_DIR / "field-table.json"
PARITY_EXCEPTIONS_JSON = ADAPTER_DIR / "parity-exceptions.json"

# The contract version the shim's code carries (hook-shim.py's CONTRACT_VERSION).
CONTRACT_VERSION = "1"

CONVERSATION_ID = "conv-0000-4000-8000-01"
SESSION_ID_FIELD = "conv-0000-4000-8000-02"
GENERATION_ID = "conv-0000-4000-8000-03"
PARENT_CONVERSATION_ID = "conv-0000-4000-8000-04"
CHILD_CONVERSATION_ID = "conv-0000-4000-8000-05"
TOOL_USE_ID = "tool-use-placeholder"
SUBAGENT_ID = "subagent-placeholder"
TRANSCRIPT_PATH_NAME = "transcript.jsonl"

# Environment names that mark a Claude Code process. The shim removes them from a hook's environment.
CLAUDE_PROCESS_ENV_NAMES = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_PID")

DELIVERED_PHASE_NUMBER = 2


def registration_key(event: str, matcher: str, script: str) -> str:
    """The key `event|matcher|script` that names one settings.json hook registration. An absent matcher is `*`."""
    return f"{event}|{matcher or '*'}|{script}"


def settings_registrations() -> list[tuple[str, str, str]]:
    """(event, matcher, script) for every hook command in settings.json, in file order. An absent matcher is `*`."""
    triples = []
    for event, entries in load_json(SETTINGS_JSON)["hooks"].items():
        for entry in entries:
            for hook in entry["hooks"]:
                triples.append((event, entry.get("matcher") or "*", hook["command"].rsplit("/", 1)[-1]))
    return triples


def parse_registration_key(key: str) -> tuple[str, str, str]:
    """Split a registration key `event|matcher|script`. The matcher holds `|`, so the script is taken from the end."""
    event, rest = key.split("|", 1)
    matcher, script = rest.rsplit("|", 1)
    return event, matcher, script


def phase_number(entry: dict) -> int:
    """The leading number of a `pending-phase` entry's phase, such as 5 for "5b"."""
    match = re.match(r"\d+", entry["phase"])
    assert match, f"phase {entry['phase']!r} does not start with a number"
    return int(match.group())


def symlink_bin_dir(parent: Path, *names: str, dirname: str = "bin-only") -> Path:
    directory = parent / dirname
    directory.mkdir(exist_ok=True)
    for name in names:
        found = shutil.which(name)
        assert found, name
        link = directory / name
        if not link.exists():
            link.symlink_to(found)
    return directory


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def shim_argv(*args: str, shim: Path = SHIM) -> list[str]:
    """The argv that starts the shim as the generated command does: this interpreter, `-I -S`, the shim, then `args`."""
    return [sys.executable, *SHIM_INTERPRETER_FLAGS, str(shim), *args]


def compile_shim_module(shim: Path = SHIM) -> types.ModuleType:
    """Compiles the shim's source into a fresh module registered under SHIM_MODULE_NAME. Writes no bytecode file."""
    module = types.ModuleType(SHIM_MODULE_NAME)
    module.__file__ = str(shim)
    sys.modules[SHIM_MODULE_NAME] = module
    exec(compile(Path(shim).read_bytes(), str(shim), "exec"), module.__dict__)  # loading the file under test
    return module


@functools.cache
def _default_shim_module() -> types.ModuleType:
    return compile_shim_module(SHIM)


def load_shim(shim: Path = SHIM) -> types.ModuleType:
    """The shim loaded in-process. The default file is compiled once per process, and any other path anew on each call.

    A test that changes a module attribute does so through `monkeypatch`, which restores it."""
    if Path(shim) == SHIM:
        module = _default_shim_module()
        sys.modules[SHIM_MODULE_NAME] = module
        return module
    return compile_shim_module(shim)


@dataclass(frozen=True)
class PayloadSpec:
    """One Cursor payload, its Claude twin, and the files it needs on disk."""

    spec_id: str
    cursor: dict
    claude_twin: dict
    # Files to create before the call, as {path: text}.
    setup_files: dict = field(default_factory=dict)


def _cursor_base(event: str, root: Path, **extra) -> dict:
    return {
        "conversation_id": CONVERSATION_ID,
        "generation_id": GENERATION_ID,
        "model": "placeholder-model",
        "hook_event_name": event,
        "cursor_version": "0.0.0",
        "workspace_roots": [str(root)],
        "user_email": None,
        **extra,
    }


def _claude_base(event: str, root: Path, session_id: str = CONVERSATION_ID, **extra) -> dict:
    return {"hook_event_name": event, "session_id": session_id, "cwd": str(root), **extra}


def _target(root: Path) -> str:
    return str(root / "src" / "target.txt")


def _build_shell_pre(root: Path) -> PayloadSpec:
    command = "echo hello"
    cursor = _cursor_base(
        "preToolUse",
        root,
        session_id=SESSION_ID_FIELD,
        tool_name="Shell",
        tool_input={"command": command, "cwd": ""},
        tool_use_id=TOOL_USE_ID,
        cwd="",
    )
    twin = _claude_base("PreToolUse", root, tool_name="Bash", tool_input={"command": command}, tool_use_id=TOOL_USE_ID)
    return PayloadSpec("shell_pre", cursor, twin)


def _build_shell_pre_child(root: Path) -> PayloadSpec:
    """A subagent's Shell call: its own conversation id, and the parent's in parent_agent_id."""
    spec = _build_shell_pre(root)
    cursor = {**spec.cursor, "conversation_id": CHILD_CONVERSATION_ID, "parent_agent_id": PARENT_CONVERSATION_ID}
    twin = {**spec.claude_twin, "session_id": PARENT_CONVERSATION_ID}
    return PayloadSpec("shell_pre_child", cursor, twin)


def _build_shell_before_execution(root: Path) -> PayloadSpec:
    command = "echo hello"
    cursor = _cursor_base("beforeShellExecution", root, command=command, cwd="", sandbox=False)
    twin = _claude_base("PreToolUse", root, tool_name="Bash", tool_input={"command": command})
    return PayloadSpec("shell_before_execution", cursor, twin)


def _build_shell_post(root: Path) -> PayloadSpec:
    command = "echo hello"
    cursor = _cursor_base(
        "postToolUse",
        root,
        session_id=SESSION_ID_FIELD,
        tool_name="Shell",
        tool_input={"command": command, "cwd": ""},
        tool_output=json.dumps({"exitCode": 0, "stdout": "hello\n"}),
        tool_use_id=TOOL_USE_ID,
        cwd="",
        duration=12,
    )
    twin = _claude_base(
        "PostToolUse",
        root,
        tool_name="Bash",
        tool_input={"command": command},
        tool_response={"exitCode": 0, "stdout": "hello\n"},
        tool_use_id=TOOL_USE_ID,
    )
    return PayloadSpec("shell_post", cursor, twin)


# A Read result is plain text, not JSON text.
READ_RESULT_TEXT = "plain file text, not JSON\n"


def _build_read_post(root: Path) -> PayloadSpec:
    path = _target(root)
    cursor = _cursor_base(
        "postToolUse",
        root,
        session_id=SESSION_ID_FIELD,
        tool_name="Read",
        tool_input={"file_path": path},
        tool_output=READ_RESULT_TEXT,
        tool_use_id=TOOL_USE_ID,
        cwd="",
        duration=3,
    )
    twin = _claude_base(
        "PostToolUse",
        root,
        tool_name="Read",
        tool_input={"file_path": path},
        tool_response=READ_RESULT_TEXT,
        tool_use_id=TOOL_USE_ID,
    )
    return PayloadSpec("read_post", cursor, twin)


# A Grep result that holds a credential-shaped string and is not JSON text.
GREP_RESULT_TEXT = "src/a.txt:1:token = " + "ghp_" + "A1b2C3d4" * 5 + "\n"


def _build_grep_post(root: Path) -> PayloadSpec:
    tool_input = {"pattern": "needle", "output_mode": "content", "glob": "*.txt"}
    cursor = _cursor_base(
        "postToolUse",
        root,
        session_id=SESSION_ID_FIELD,
        tool_name="Grep",
        tool_input=tool_input,
        tool_output=GREP_RESULT_TEXT,
        tool_use_id=TOOL_USE_ID,
        cwd="",
        duration=4,
    )
    twin = _claude_base(
        "PostToolUse",
        root,
        tool_name="Grep",
        tool_input=dict(tool_input),
        tool_response=GREP_RESULT_TEXT,
        tool_use_id=TOOL_USE_ID,
    )
    return PayloadSpec("grep_post", cursor, twin)


def _build_read_pre(root: Path) -> PayloadSpec:
    path = _target(root)
    cursor = _cursor_base(
        "preToolUse",
        root,
        session_id=SESSION_ID_FIELD,
        tool_name="Read",
        tool_input={"file_path": path},
        tool_use_id=TOOL_USE_ID,
        cwd="",
    )
    twin = _claude_base("PreToolUse", root, tool_name="Read", tool_input={"file_path": path}, tool_use_id=TOOL_USE_ID)
    return PayloadSpec("read_pre", cursor, twin)


def _build_read_before_file(root: Path) -> PayloadSpec:
    path = _target(root)
    cursor = _cursor_base(
        "beforeReadFile",
        root,
        file_path=path,
        content="placeholder file text",
        attachments=[{"type": "file", "file_path": path}],
    )
    twin = _claude_base("PreToolUse", root, tool_name="Read", tool_input={"file_path": path})
    return PayloadSpec("read_before_file", cursor, twin)


def _build_write_create(root: Path) -> PayloadSpec:
    path = _target(root)
    cursor = _cursor_base(
        "preToolUse",
        root,
        session_id=SESSION_ID_FIELD,
        tool_name="Write",
        tool_input={"file_path": path, "content": "new text"},
        tool_use_id=TOOL_USE_ID,
        cwd="",
    )
    twin = _claude_base(
        "PreToolUse", root, tool_name="Write", tool_input={"file_path": path, "content": "new text"}, tool_use_id=TOOL_USE_ID
    )
    return PayloadSpec("write_create", cursor, twin)


def _build_write_edit(root: Path) -> PayloadSpec:
    """The same Write call as write_create, over a file that already holds text.

    The twin is the literal `Edit` for an `Edit`-selecting matcher, with a `tool_input` of `file_path` and `content` alone.
    It is pinned to the shim's naming, not a payload Claude Code sends, because Claude Code never sends an `Edit` that
    carries `content`. The payload check probes this spec under an `Edit`-selecting `--matcher`."""
    path = _target(root)
    created = _build_write_create(root)
    twin = {**created.claude_twin, "tool_name": "Edit", "tool_input": {"file_path": path, "content": "new text"}}
    return PayloadSpec("write_edit", created.cursor, twin, setup_files={Path(path): "old text"})


# A Write result is plain text, not JSON text.
WRITE_RESULT_TEXT = "wrote the file\n"


def _build_write_post(root: Path) -> PayloadSpec:
    """A Write's postToolUse over the file it just wrote, so the file exists when the shim sees the call."""
    path = _target(root)
    tool_input = {"file_path": path, "content": "new text"}
    cursor = _cursor_base(
        "postToolUse",
        root,
        session_id=SESSION_ID_FIELD,
        tool_name="Write",
        tool_input=tool_input,
        tool_output=WRITE_RESULT_TEXT,
        tool_use_id=TOOL_USE_ID,
        cwd="",
        duration=5,
    )
    twin = _claude_base(
        "PostToolUse",
        root,
        tool_name="Write",
        tool_input=dict(tool_input),
        tool_response=WRITE_RESULT_TEXT,
        tool_use_id=TOOL_USE_ID,
    )
    return PayloadSpec("write_post", cursor, twin, setup_files={Path(path): "new text"})


def _build_delete_pre(root: Path) -> PayloadSpec:
    path = _target(root)
    cursor = _cursor_base(
        "preToolUse",
        root,
        session_id=SESSION_ID_FIELD,
        tool_name="Delete",
        tool_input={"file_path": path},
        tool_use_id=TOOL_USE_ID,
        cwd="",
    )
    twin = _claude_base("PreToolUse", root, tool_name="Bash", tool_input={"command": f"rm -- '{path}'"}, tool_use_id=TOOL_USE_ID)
    return PayloadSpec("delete_pre", cursor, twin)


def _build_grep_pre(root: Path) -> PayloadSpec:
    tool_input = {"pattern": "needle", "output_mode": "content", "glob": "*.txt"}
    cursor = _cursor_base(
        "preToolUse",
        root,
        session_id=SESSION_ID_FIELD,
        tool_name="Grep",
        tool_input=tool_input,
        tool_use_id=TOOL_USE_ID,
        cwd="",
    )
    twin = _claude_base("PreToolUse", root, tool_name="Grep", tool_input=dict(tool_input), tool_use_id=TOOL_USE_ID)
    return PayloadSpec("grep_pre", cursor, twin)


def _build_task_pre(root: Path) -> PayloadSpec:
    tool_input = {"description": "placeholder task", "prompt": "placeholder prompt", "subagent_type": "placeholder-agent"}
    cursor = _cursor_base(
        "preToolUse",
        root,
        session_id=SESSION_ID_FIELD,
        tool_name="Task",
        tool_input=tool_input,
        tool_use_id=TOOL_USE_ID,
        cwd="",
    )
    twin = _claude_base("PreToolUse", root, tool_name="Agent", tool_input=dict(tool_input), tool_use_id=TOOL_USE_ID)
    return PayloadSpec("task_pre", cursor, twin)


def _build_prompt_submit(root: Path) -> PayloadSpec:
    cursor = _cursor_base("beforeSubmitPrompt", root, prompt="placeholder prompt text", attachments=[])
    twin = _claude_base("UserPromptSubmit", root, prompt="placeholder prompt text")
    return PayloadSpec("prompt_submit", cursor, twin)


def _build_session_start(root: Path) -> PayloadSpec:
    cursor = _cursor_base("sessionStart", root, session_id=SESSION_ID_FIELD, is_background_agent=False, composer_mode="agent")
    twin = _claude_base("SessionStart", root, source="startup")
    return PayloadSpec("session_start", cursor, twin)


def _build_subagent_start(root: Path) -> PayloadSpec:
    cursor = _cursor_base(
        "subagentStart",
        root,
        subagent_id=SUBAGENT_ID,
        subagent_type="placeholder-agent",
        subagent_model="placeholder-model",
        task="placeholder task",
        tool_call_id=SUBAGENT_ID,
        is_parallel_worker=False,
        parent_conversation_id=CONVERSATION_ID,
        parent_agent_id=CONVERSATION_ID,
    )
    twin = _claude_base("SubagentStart", root, agent_type="placeholder-agent", agent_id=SUBAGENT_ID)
    return PayloadSpec("subagent_start", cursor, twin)


def _build_subagent_stop(root: Path) -> PayloadSpec:
    cursor = _cursor_base(
        "subagentStop",
        root,
        subagent_type="placeholder-agent",
        status="completed",
        task="placeholder task",
        duration_ms=1000,
        tool_call_count=1,
        message_count=2,
        child_conversation_id=CHILD_CONVERSATION_ID,
        parent_agent_id=CONVERSATION_ID,
    )
    twin = _claude_base("SubagentStop", root, agent_type="placeholder-agent")
    return PayloadSpec("subagent_stop", cursor, twin)


def _build_stop(root: Path) -> PayloadSpec:
    transcript = str(root / TRANSCRIPT_PATH_NAME)
    cursor = _cursor_base("stop", root, status="completed", loop_count=0, transcript_path=transcript)
    twin = _claude_base("Stop", root, transcript_path=transcript)
    return PayloadSpec("stop", cursor, twin)


def _build_after_agent_response(root: Path) -> PayloadSpec:
    transcript = str(root / TRANSCRIPT_PATH_NAME)
    cursor = _cursor_base("afterAgentResponse", root, text="placeholder response", transcript_path=transcript)
    twin = _claude_base(
        "Stop", root, transcript_path=transcript, last_assistant_message="placeholder response", prompt_id=GENERATION_ID
    )
    return PayloadSpec("after_agent_response", cursor, twin)


_BUILDERS = {
    "shell_pre": _build_shell_pre,
    "shell_pre_child": _build_shell_pre_child,
    "shell_before_execution": _build_shell_before_execution,
    "shell_post": _build_shell_post,
    "read_post": _build_read_post,
    "grep_post": _build_grep_post,
    "read_pre": _build_read_pre,
    "read_before_file": _build_read_before_file,
    "write_create": _build_write_create,
    "write_edit": _build_write_edit,
    "write_post": _build_write_post,
    "delete_pre": _build_delete_pre,
    "grep_pre": _build_grep_pre,
    "task_pre": _build_task_pre,
    "prompt_submit": _build_prompt_submit,
    "session_start": _build_session_start,
    "subagent_start": _build_subagent_start,
    "subagent_stop": _build_subagent_stop,
    "stop": _build_stop,
    "after_agent_response": _build_after_agent_response,
}

SPEC_IDS = tuple(_BUILDERS)

# The Claude event and matcher target each spec reaches, for tests that register the shim against it.
SPEC_CLAUDE_EVENT = {
    "shell_pre": "PreToolUse",
    "shell_pre_child": "PreToolUse",
    "shell_before_execution": "PreToolUse",
    "shell_post": "PostToolUse",
    "read_post": "PostToolUse",
    "grep_post": "PostToolUse",
    "read_pre": "PreToolUse",
    "read_before_file": "PreToolUse",
    "write_create": "PreToolUse",
    "write_edit": "PreToolUse",
    "write_post": "PostToolUse",
    "delete_pre": "PreToolUse",
    "grep_pre": "PreToolUse",
    "task_pre": "PreToolUse",
    "prompt_submit": "UserPromptSubmit",
    "session_start": "SessionStart",
    "subagent_start": "SubagentStart",
    "subagent_stop": "SubagentStop",
    "stop": "Stop",
    "after_agent_response": "Stop",
}


def build_spec(spec_id: str, root: Path) -> PayloadSpec:
    """The spec's Cursor payload and Claude twin, with `root` as the one workspace root and the working directory."""
    return _BUILDERS[spec_id](Path(root))


def shell_pre_payload(root: Path, command: str, **tool_input_extra) -> dict:
    """A preToolUse Shell payload with an arbitrary command, for tests that vary the command."""
    payload = copy.deepcopy(build_spec("shell_pre", root).cursor)
    payload["tool_input"] = {"command": command, **tool_input_extra}
    return payload


def file_tool_payload(root: Path, tool: str, file_path: str, *, target_key: str = "file_path", **tool_input_extra) -> dict:
    """A preToolUse payload for Read, Write, Delete or Grep aimed at `file_path`, named `target_key` in tool_input.

    A Grep call may name its target `path` instead of `file_path`."""
    spec_id = {"Read": "read_pre", "Write": "write_create", "Delete": "delete_pre", "Grep": "grep_pre"}[tool]
    payload = copy.deepcopy(build_spec(spec_id, root).cursor)
    payload["tool_input"] = {**payload["tool_input"], target_key: file_path, **tool_input_extra}
    return payload


# --- shim harness ---


@dataclass(frozen=True)
class ShimEnv:
    """A temporary home holding an install record, and the routed config dir it names."""

    home: Path
    config_dir: Path
    record_path: Path

    @property
    def state_dir(self) -> Path:
        return self.config_dir / "cursor-adapter-state"

    @property
    def log_path(self) -> Path:
        return self.state_dir / "shim.log"


def recorded_tool_paths() -> dict:
    """Absolute paths of the tools the install record names, from the host."""
    paths = {}
    for name in ("bash", "jq", "git", "timeout", "gtimeout", "gh", "python3"):
        found = shutil.which(name)
        if found:
            paths[name] = found
    return paths


def make_shim_env(
    tmp_path: Path, *, tool_map: dict | None = None, record: dict | None = None, tool_paths: dict | None = None
) -> ShimEnv:
    """Create home/.cursor/cursor-adapter/install.json and the routed config dir under tmp_path.

    `record`, when given, replaces the whole record, so a test builds a malformed one.
    `tool_paths` replaces the recorded tool paths, so a test can put stubs on the PATH the shim fixes.
    """
    home = tmp_path / "home"
    config_dir = tmp_path / "config"
    record_dir = home / ".cursor" / "cursor-adapter"
    record_dir.mkdir(parents=True)
    config_dir.mkdir()
    # `install` creates the state dir, 0700, and the shim only logs into it.
    (config_dir / "cursor-adapter-state").mkdir(mode=0o700)
    record_path = record_dir / "install.json"
    if record is None:
        record = {
            "routed_config_dir": str(config_dir),
            "contract_version": CONTRACT_VERSION,
            "tool_paths": tool_paths if tool_paths is not None else recorded_tool_paths(),
            "tool_map": tool_map if tool_map is not None else load_json(TOOL_MAP_JSON),
        }
    record_path.write_text(json.dumps(record), encoding="utf-8")
    return ShimEnv(home=home, config_dir=config_dir, record_path=record_path)


def copy_adapter(destination: Path, *, field_table: dict | None = None, parity: dict | None = None) -> Path:
    """Copy the adapter directory to `destination/scripts/cursor-adapter` and return the copied shim's path.

    The shim reads `field-table.json` and `parity-exceptions.json` beside itself, so `field_table` and `parity`
    replace those two files in the copy."""
    adapter_copy = destination / "scripts" / "cursor-adapter"
    shutil.copytree(ADAPTER_DIR, adapter_copy, ignore=shutil.ignore_patterns("__pycache__"))
    if field_table is not None:
        (adapter_copy / "field-table.json").write_text(json.dumps(field_table))
    if parity is not None:
        (adapter_copy / "parity-exceptions.json").write_text(json.dumps(parity))
    return adapter_copy / SHIM.name


def hook_args(
    claude_event: str,
    *,
    script: Path | str | None = None,
    matcher: str | None = None,
    mode: str = "hook",
    label: str = "test-hook",
    fail_closed: bool = True,
    deadline: int = 10,
    contract_version: str | None = CONTRACT_VERSION,
) -> list[str]:
    """The shim's command line. A `contract_version` of None leaves the `--contract-version` flag out."""
    args = ["--contract-version", contract_version] if contract_version is not None else []
    args += [
        "--claude-event",
        claude_event,
        "--mode",
        mode,
        "--label",
        label,
        "--deadline",
        str(deadline),
        "--fail-closed" if fail_closed else "--fail-open",
    ]
    if matcher is not None:
        args += ["--matcher", matcher]
    if script is not None:
        args += ["--script", str(script)]
    return args


def without_git_environment(environment: dict) -> dict:
    """The environment minus every GIT_* name, so an inherited GIT_DIR cannot make an outside arm an inside one."""
    return {k: v for k, v in environment.items() if not k.startswith("GIT_")}


def assert_outside_any_repository(directory: Path, environment: dict) -> None:
    """Fail unless `git rev-parse --git-dir` fails in `directory` under `environment`."""
    # git is found on the test process's PATH, since the arm's own PATH may hold nothing but stubs.
    result = subprocess.run(
        [shutil.which("git") or "git", "rev-parse", "--git-dir"],
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0, f"{directory} is inside a repository: {result.stdout}"


def shim_process_env(env: ShimEnv, extra: dict | None = None) -> dict:
    """The environment Cursor gives a hook: the host's, minus every Claude Code marker and GIT_* name, with HOME on the record."""
    process_env = without_git_environment({k: v for k, v in os.environ.items() if k not in CLAUDE_PROCESS_ENV_NAMES})
    process_env["HOME"] = str(env.home)
    process_env.pop("CLAUDE_CONFIG_DIR", None)
    if extra:
        process_env.update(extra)
    return process_env


def run_shim(
    env: ShimEnv,
    payload: dict | str | bytes,
    args: list[str],
    *,
    cwd: Path | None = None,
    extra_env: dict | None = None,
    timeout: float = 60,
    shim: Path = SHIM,
) -> subprocess.CompletedProcess:
    """Run the shim with `payload` on stdin. A dict is sent as JSON, text and bytes as given."""
    data = payload if isinstance(payload, bytes) else (payload if isinstance(payload, str) else json.dumps(payload)).encode()
    process_env = shim_process_env(env, extra_env)
    if cwd is not None:
        assert_outside_any_repository(cwd, process_env)
    return subprocess.run(
        shim_argv(*args, shim=shim),
        input=data,
        capture_output=True,
        cwd=cwd,
        env=process_env,
        check=False,
        timeout=timeout,
    )


def shim_json(result: subprocess.CompletedProcess) -> dict:
    return json.loads(result.stdout.decode())


def write_hook_script(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


def recording_probe_script(path: Path, record_to: Path, stdout_text: str = "", exit_code: int = 0) -> Path:
    body = f"cat > '{record_to}'\nprintf '%s' '{stdout_text}'\nexit {exit_code}\n"
    return write_hook_script(path, body)


def read_log_lines(env: ShimEnv) -> list[dict]:
    if not env.log_path.exists():
        return []
    return [dict(pair.split("=", 1) for pair in line.split(" ")) for line in env.log_path.read_text().splitlines()]


# A wrapper, run as `python3 -I -S -c`, that installs an audit hook and then runs the shim. The hook is never installed in
# the pytest process, because an audit hook cannot be removed. It reports the watched events on stderr, which the shim
# never writes. "main" runs the file as `__main__` through runpy; "load" compiles it with no call to `main` and reports
# how many descriptors the load opened; "call" loads it and then calls one named function.
AUDIT_WRAPPER = r"""
import contextlib, errno, json, os, re, runpy, select, selectors, signal, stat, subprocess, sys, time, types
config = json.loads(sys.argv[1])
watched = frozenset(config["events"])
blocked = frozenset(config.get("block", []))
events = []


measuring = False


def hook(event, args):
    if event in watched and not measuring:
        record = {"event": event, "path": "", "mode": "", "flags": 0}
        if event == "open":
            raw_path = os.fsdecode(args[0]) if isinstance(args[0], bytes) else args[0]
            record["path"] = raw_path if isinstance(raw_path, str) else ""
            record["mode"] = args[1] if isinstance(args[1], str) else ""
            record["flags"] = args[2] if isinstance(args[2], int) else 0
        events.append(record)
        if event in blocked:
            raise RuntimeError("blocked by the audit wrapper")


def descriptor_count():
    return len(os.listdir("/dev/fd"))


code = 0
extra = {}
if config["mode"] in ("load", "call"):
    with open(config["shim"], "rb") as handle:
        source = handle.read()
    compiled = compile(source, config["shim"], "exec")
    namespace = {"__name__": "cursor_hook_shim", "__file__": config["shim"]}
    descriptor_count()
    before = descriptor_count()
    sys.addaudithook(hook)
    exec(compiled, namespace)
    extra["descriptors_opened"] = descriptor_count() - before
    if config["mode"] == "call":
        namespace.update(config.get("overrides", {}))
        extra["result"] = namespace[config["function"]](*config["arguments"])
else:
    sys.argv = [config["shim"], *config["argv"]]
    sys.addaudithook(hook)
    try:
        runpy.run_path(config["shim"], run_name="__main__")
    except SystemExit as stop:
        code = stop.code
extra["modules"] = sorted({name.split(".")[0] for name in sys.modules})
measuring = True
try:
    with open("/proc/self/status") as status_file:
        for line in status_file:
            if line.startswith("VmHWM:"):
                extra["vm_hwm_kib"] = int(line.split()[1])
except OSError:
    pass
sys.stderr.write(json.dumps({"events": events, "extra": extra}))
sys.exit(code)
"""

PROCESS_START_EVENTS = ("subprocess.Popen", "os.exec", "os.fork", "os.posix_spawn", "os.spawn", "os.system")


def run_audited(
    argv: list[str],
    *,
    events: tuple[str, ...] = PROCESS_START_EVENTS,
    mode: str = "main",
    block: tuple[str, ...] = (),
    function: str = "",
    arguments: tuple = (),
    overrides: dict | None = None,
    shim: Path = SHIM,
    payload: bytes = b"",
    env: dict | None = None,
    cwd: Path | None = None,
    timeout: float = 60,
) -> tuple[subprocess.CompletedProcess, list[dict], dict]:
    """Runs `shim` under AUDIT_WRAPPER. Returns the process, the watched events it raised and the wrapper's extra report.

    Mode "call" loads the shim, sets the module names in `overrides`, and calls its `function` with `arguments`; the
    report's `result` holds the return value. On Linux the report's `vm_hwm_kib` is the wrapper's own peak resident size.
    Events named in `block` raise after they are recorded, so a seeded start counts without starting anything."""
    config = {
        "mode": mode,
        "shim": str(shim),
        "argv": argv,
        "events": list(events),
        "block": list(block),
        "function": function,
        "arguments": list(arguments),
        "overrides": overrides or {},
    }
    result = subprocess.run(
        [sys.executable, *SHIM_INTERPRETER_FLAGS, "-c", AUDIT_WRAPPER, json.dumps(config)],
        input=payload,
        capture_output=True,
        env=env,
        cwd=cwd,
        check=False,
        timeout=timeout,
    )
    report = json.loads(result.stderr.decode()) if result.stderr else {"events": [], "extra": {}}
    return result, report["events"], report["extra"]


def opens_of_files_like(events: list[dict], targets: list[Path], cwd: Path) -> list[dict]:
    """The `open` events whose path names the same file as one of `targets`, by real path or by file identity.

    A spelling through a link, a relative path or a `..` part all name the file, so the match is not on the spelling."""
    real_paths = {os.path.realpath(target) for target in targets}
    identities = []
    for target in targets:
        with contextlib.suppress(OSError):
            identities.append(os.stat(target))
    matched = []
    for event in events:
        if not event["path"]:
            continue
        candidate = os.path.join(cwd, event["path"])
        if os.path.realpath(candidate) in real_paths:
            matched.append(event)
            continue
        with contextlib.suppress(OSError):
            if any(os.path.samestat(os.stat(candidate), identity) for identity in identities):
                matched.append(event)
    return matched


# Non-ASCII, separator and control values that are probes where an ASCII-only grammar and a Unicode-aware one differ.
# A test runs each one at the start, in the middle and at the end of an otherwise valid input, and writes each
# classifier's expected outcome per position.
# A lone surrogate also appears as an object key in the parser rows.
TEXT_RULE_CORPUS = {
    "nbsp": " ",
    "line-separator": " ",
    "paragraph-separator": " ",
    "next-line": "\u0085",
    "carriage-return": "\r",
    "vertical-tab": "\x0b",
    "form-feed": "\x0c",
    "file-separator": "\x1c",
    "group-separator": "\x1d",
    "record-separator": "\x1e",
    "unit-separator": "\x1f",
    "nul": "\x00",
    "trailing-newline": "\n",
    "unicode-digit": "٣",
    "kelvin-sign": "K",
    "long-s": "ſ",
    "e-acute": "é",
    "fullwidth-latin": "Ａ",
    "ideographic-space": "　",
    "lone-surrogate": "\ud800",
}


def embed_at_each_position(valid: str, value: str) -> list[str]:
    """`valid` with `value` at its start, in the middle and at its end."""
    middle = len(valid) // 2
    return [value + valid, valid[:middle] + value + valid[middle:], valid + value]
