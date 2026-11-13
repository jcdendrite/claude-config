"""Helpers the replay_bench checks tests share. Log lines and transcript entries keep the key structure of the
shapes a real `claude -p` run writes, with every value synthetic. No test launches `claude`.
"""
from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest
from replay_bench import checks
from replay_bench.checks import (
    InstructionLog,
    LoadExpectation,
    ToolkitPaths,
)
from review_bench import runner

SESSION_ID = "synthetic-session-id"
OTHER_SESSION_ID = "synthetic-other-session-id"
AGENT_ID = "synthetic-agent-id"
SESSION_START, PATH_GLOB_MATCH = checks.LOAD_REASON_SESSION_START, checks.LOAD_REASON_PATH_GLOB_MATCH


class _Absent:
    def __repr__(self) -> str:
        return "ABSENT"


ABSENT = _Absent()


@dataclass(frozen=True)
class World:
    root: Path
    config_dir: Path
    fixture_dir: Path
    other_fixture_dir: Path
    home: Path
    local_dir: Path
    live_checkout: Path
    allow_file: Path
    second_allow_file: Path
    toolkit: ToolkitPaths

    @property
    def claude_md(self) -> Path:
        return self.config_dir / "CLAUDE.md"

    @property
    def log_path(self) -> Path:
        return self.root / "instructions-loaded.jsonl"

    @property
    def projects_root(self) -> Path:
        return self.config_dir / "projects"

    @property
    def own_session_paths(self) -> tuple[Path, Path]:
        return runner.own_session_paths_for(self.projects_root / "synthetic-project" / f"{SESSION_ID}.jsonl")

    def rule(self, name: str = "python.md") -> str:
        return str(self.config_dir / "rules" / name)


def _touch(path: Path, text: str = "synthetic\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def build_world(tmp_path: Path) -> World:
    home = tmp_path / "home"
    config_dir = home / ".claude"
    _touch(config_dir / "CLAUDE.md")
    _touch(config_dir / "rules" / "python.md")
    _touch(config_dir / "rules" / "nested" / "deep.md")
    _touch(config_dir / "rules-extra" / "x.md")
    _touch(config_dir / "agents" / "reviewer.md")
    _touch(config_dir / "skills" / "demo" / "SKILL.md")
    (config_dir / "sessions").mkdir()
    personal_agents = tmp_path / "personal" / ".claude" / "agents"
    allow_file = _touch(personal_agents / "extra.md")
    second_allow_file = _touch(personal_agents / "second.md")
    fixture_dir = tmp_path / "tmp" / f"{runner.FIXTURE_DIR_PREFIX}replay-aaa"
    other_fixture_dir = tmp_path / "tmp" / f"{runner.FIXTURE_DIR_PREFIX}replay-bbb"
    for directory in (fixture_dir, other_fixture_dir):
        _touch(directory / "src" / "app.py")
    _touch(fixture_dir / "docs" / "NOTES.md")
    live_checkout = tmp_path / "live"
    _touch(live_checkout / "src" / "app.py")
    return World(
        root=tmp_path, config_dir=config_dir, fixture_dir=fixture_dir, other_fixture_dir=other_fixture_dir,
        home=home, local_dir=tmp_path / "replay" / ".local", live_checkout=live_checkout,
        allow_file=allow_file, second_allow_file=second_allow_file,
        toolkit=ToolkitPaths.from_config_dir(config_dir, [allow_file, second_allow_file]),
    )


# --- log helpers -------------------------------------------------------------


def _line(file_path: str, load_reason: str = SESSION_START, *, agent_id: object = ABSENT, **overrides: object) -> dict:
    """A captured dispatcher line, or a subagent line when `agent_id` is given. An override of `ABSENT`
    removes its key."""
    line: dict = {
        "session_id": SESSION_ID, "transcript_path": "synthetic", "cwd": "synthetic",
        "hook_event_name": "synthetic", "file_path": file_path, "memory_type": "synthetic", "load_reason": load_reason,
    }
    if agent_id is not ABSENT:
        line.update(
            prompt_id="synthetic", agent_id=agent_id, agent_type="synthetic", effort={"level": "synthetic"},
            globs=["synthetic"],
        )
    line.update(overrides)
    return {key: value for key, value in line.items() if value is not ABSENT}


def _parse(world: World, lines: Sequence[dict | str], *, trailing_newline: bool = True) -> InstructionLog:
    text = "\n".join(line if isinstance(line, str) else json.dumps(line) for line in lines)
    world.log_path.write_text(text + ("\n" if trailing_newline else ""))
    return checks.parse_instruction_log(world.log_path, session_id=SESSION_ID)


def _dispatcher_claude_md(world: World) -> dict:
    return _line(str(world.claude_md))


def _bare(world: World, **kwargs) -> LoadExpectation:
    return LoadExpectation(world.claude_md, True, **kwargs)


def _toolkit_arm(world: World, **kwargs) -> LoadExpectation:
    return LoadExpectation(world.claude_md, False, **kwargs)


def _check(world: World, lines: Sequence[dict], expectation: LoadExpectation):
    return checks.check_instruction_loads(
        _parse(world, lines), expectation, toolkit=world.toolkit, fixture_dir=world.fixture_dir,
    )


# --- transcript helpers ------------------------------------------------------


def _assistant(blocks: Sequence[dict], *, usage: dict | None = None) -> dict:
    default_usage = {"input_tokens": 11, "cache_creation_input_tokens": 22, "cache_read_input_tokens": 33, "output_tokens": 1}
    return {
        "type": "assistant", "isSidechain": True, "agentId": AGENT_ID,
        "message": {"role": "assistant", "content": blocks, "usage": default_usage if usage is None else usage},
    }


def _tool_use(tool_use_id: str, name: str, tool_input: dict) -> dict:
    return {"type": "tool_use", "id": tool_use_id, "name": name, "input": tool_input}


def _result(tool_use_id: str, *, denied: bool = False, kind: str = checks.NON_EXECUTION_PERMISSION_RULE) -> dict:
    block: dict = {"type": "tool_result", "tool_use_id": tool_use_id, "content": "synthetic"}
    if denied:
        block["tool_result_meta"] = [{"non_execution_kind": kind}]
    return {"type": "user", "message": {"role": "user", "content": [block]}}


def denied_result_entry(tool_use_id: str, path: str) -> dict:
    """A user entry holding one denied `tool_result`, in the shape a `claude -p --permission-mode acceptEdits`
    session records on CLI 2.1.323: the denial markers sit on the entry, and the block carries no `tool_result_meta`."""
    message = f"Claude requested permissions to write to {path}, but you haven't granted it yet."
    return {
        "type": "user",
        "message": {"role": "user", "content": [
            {"type": "tool_result", "content": message, "is_error": True, "tool_use_id": tool_use_id},
        ]},
        "toolUseResult": f"Error: {message}",
        "toolDenialKind": "user-rejected",
        "permissionDecision": {"decision": "reject", "source": "config", "reasonCode": "workingDir"},
        "sourceToolAssistantUUID": str(uuid.UUID(int=1)),
        "sessionId": str(uuid.UUID(int=2)),
        "version": "synthetic",
    }


def _call_and_result(tool_use_id: str, name: str, tool_input: dict, *, denied: bool = False) -> list[dict]:
    return [_assistant([_tool_use(tool_use_id, name, tool_input)]), _result(tool_use_id, denied=denied)]


def _send_back_attachment() -> dict:
    return {"type": "attachment", "attachment": {"type": "hook_blocking_error", "hookName": "SubagentStop"}}


def _transcript(tmp_path: Path, entries: Sequence[dict | str]) -> checks.SubagentTranscript:
    path = tmp_path / "subagent.jsonl"
    path.write_text("".join((entry if isinstance(entry, str) else json.dumps(entry)) + "\n" for entry in entries))
    return checks.read_subagent_transcript(path)


def _path_check(
    world: World, transcript: checks.SubagentTranscript, *, fixture_dir: Path | None = None,
    live_checkout: Path | None = None, config_dir: Path | None = None,
) -> checks.PathCheck:
    config_dir = config_dir or world.config_dir
    return checks.check_tool_call_paths(
        transcript, fixture_dir=fixture_dir or world.fixture_dir, config_dir=config_dir,
        projects_root=config_dir / "projects", own_session_paths=world.own_session_paths, local_dir=world.local_dir,
        live_checkout_roots=(live_checkout or world.live_checkout,), changed_relpaths=("src/app.py",),
    )


def _bash_transcript(tmp_path: Path, command: object) -> checks.SubagentTranscript:
    return _transcript(tmp_path, _call_and_result("toolu_1", "Bash", {"command": command}))


def call_with_deadline[ResultT](function: Callable[[], ResultT], *, deadline_s: float, hang_message: str) -> ResultT:
    """Runs `function` in a daemon thread and fails with `hang_message` when it has not returned by the deadline,
    so a call that blocks forever fails the test instead of hanging the suite."""
    results: list[ResultT] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            results.append(function())
        except BaseException as exc:  # re-raised in the caller's thread below
            errors.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(deadline_s)
    if worker.is_alive():
        pytest.fail(hang_message)
    if errors:
        raise errors[0]
    return results[0]


def _link(link: Path, target: Path) -> Path:
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)
    return link
