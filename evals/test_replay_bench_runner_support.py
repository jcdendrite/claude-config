"""Helpers the replay_bench runner, campaign, smoke and CLI tests share. A scripted launch stands in for
`claude`: it writes a synthetic dispatcher transcript, subagent sidecar, hook log and account session file
under a `tmp_path` config dir, with every value synthetic. No test launches `claude`.
"""
from __future__ import annotations

import inspect
import json
import os
import re
import shlex
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from replay_bench import arms, attempt, tasks
from replay_bench.account import Account
from replay_bench.spend import SpendFile
from review_bench import runner as review_runner
from review_bench.runner import EnvironmentRecord, RunStore
from test_replay_bench_checks_support import _line
from test_replay_bench_tasks import _app_with, _change, _make_repo, _mine

MODEL_ID = review_runner.REVIEWER_MODEL_ID
CLAUDE_MD_TEXT = "x" * 8000
RULE_TEXT = "---\npaths:\n  - '**/*.py'\n---\nrule\n"
CODE_WRITER_MARKDOWN = (
    "---\nname: code-writer\ndescription: Implements a change,\n  then reviews it.\n"
    "tools: Read, Edit, Write, Bash, Grep, Glob\nmodel: sonnet\neffort: high\n---\n\n"
    + "You are `code-writer`. " * 20 + "\n"
)
SETTINGS_WITH_SESSION_HOOK = {"hooks": {"SessionStart": [{
    "hooks": [{"type": "command", "command": "~/.claude/hooks/capture-session-id.sh"}],
}]}}
DISPATCH_TOOL_ID = "toolu_dispatch"
AGENT_ID = "synthetic-agent-id"
TOOLKIT_FIRST_TURN_SIZE = 6000
BARE_FIRST_TURN_SIZE = 1000
READING = EnvironmentRecord("2.1.322", "synthetic-commit")
# Fixture files whose presence at launch time each arm's wiring decides.
FIXTURE_FILES_AT_LAUNCH = (".claude/settings.json", ".claude/agents/bare-writer.md", ".claude/worktree-optout")


def write_executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


@dataclass
class Scenario:
    """What one scripted launch writes. The defaults are a clean run of the given arm."""

    # A list of log lines, or a function of the session ID (and optionally the fixture dir).
    log_lines: list[dict] | Callable[..., list[dict]] | None = None
    forged_log_lines: list[dict] | Callable[..., list[dict]] | None = None
    # (tool name, input, denied) per call, or a function of the launch call.
    tool_calls: list[tuple[str, dict, bool]] | Callable[..., list[tuple[str, dict, bool]]] | None = None
    edit: Callable[[Path], None] | None = None
    cost: float | None = 0.5
    timed_out: bool = False
    no_output: bool = False
    raises: BaseException | None = None
    first_turn_size: int | None = None
    damage_sidecar: str | None = None  # "skip" puts a non-JSON line before the last line, "torn" tears the last line
    write_account_session_file: bool = True
    observed_model: str = MODEL_ID
    # Runs after the session is written and before the launch returns, with the call.
    after_write: Callable[..., None] | None = None


@dataclass
class LaunchCall:
    cmd: list[str]
    cwd: Path
    session_id: str
    agent_name: str
    log_path: Path
    inner_prompt: str = ""
    config_dir: Path | None = None
    # The fixture's contents when the launch began, before the scenario wrote anything.
    fixture_files: dict[str, str | None] = field(default_factory=dict)
    fixture_status: str = ""


def log_path_from_command(cmd: list[str]) -> Path:
    settings = json.loads(cmd[cmd.index("--settings") + 1])
    hook_command = settings["hooks"]["InstructionsLoaded"][0]["hooks"][0]["command"]
    return Path(shlex.split(hook_command)[-1])


def agent_name_from_command(cmd: list[str]) -> str:
    prompt = cmd[cmd.index("-p") + 1]
    return re.search(r"dispatch the `([^`]+)` agent", prompt).group(1)


def inner_prompt_from_command(cmd: list[str]) -> str:
    prompt = cmd[cmd.index("-p") + 1]
    return prompt.split("<<<\n", 1)[1].rsplit("\n>>>", 1)[0]


def config_dir_from_command(cmd: list[str], default: Path) -> Path:
    prefix = "CLAUDE_CONFIG_DIR="
    return next((Path(part[len(prefix):]) for part in cmd if part.startswith(prefix)), default)


def stream_lines(cost: float | None) -> list[bytes]:
    result = {"type": "result", "subtype": "success", "is_error": False, "terminal_reason": "completed"}
    if cost is not None:
        result.update(total_cost_usd=cost, usage={"input_tokens": 3, "output_tokens": 4})
    return [json.dumps({"type": "system"}).encode(), json.dumps(result).encode()]


@dataclass
class ScriptedLaunch:
    """A `launch=` stand-in. `scenario_for(call_number, agent_name)` picks each call's behavior; the default
    is a clean run. The account's config dir comes from the launch prefix, so two accounts work side by side."""

    config_dir: Path
    scenario_for: Callable[[int, str], Scenario] | None = None
    # Picks a scenario from the whole call, such as by the task its inner prompt names. It wins over `scenario_for`.
    by_call: Callable[[LaunchCall], Scenario] | None = None
    calls: list[LaunchCall] = field(default_factory=list)

    def __call__(self, cmd: list[str], cwd: Path, timeout_s: int) -> tuple[list[bytes], bool]:
        session_id = cmd[cmd.index("--session-id") + 1]
        agent_name = agent_name_from_command(cmd)
        call = LaunchCall(
            cmd, cwd, session_id, agent_name, log_path_from_command(cmd), inner_prompt_from_command(cmd),
            config_dir_from_command(cmd, self.config_dir),
        )
        call.fixture_files = {
            relative: (cwd / relative).read_text() if (cwd / relative).exists() else None
            for relative in FIXTURE_FILES_AT_LAUNCH
        }
        call.fixture_status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=cwd, capture_output=True, text=True, check=False,
        ).stdout
        number = len(self.calls)
        self.calls.append(call)
        if self.by_call is not None:
            scenario = self.by_call(call)
        else:
            scenario = self.scenario_for(number, agent_name) if self.scenario_for else Scenario()
        if scenario.raises is not None:
            raise scenario.raises
        if scenario.no_output:
            return [], False
        self._write_session(call, scenario)
        return stream_lines(scenario.cost), scenario.timed_out

    def _default_log(self, call: LaunchCall) -> list[dict]:
        lines = [_line(str(call.config_dir / "CLAUDE.md"), session_id=call.session_id)]
        if call.agent_name == arms.TOOLKIT.agent_name:
            lines.append(_line(
                str(call.config_dir / "rules" / "python.md"), "path_glob_match", agent_id=AGENT_ID,
                session_id=call.session_id, trigger_file_path=str(call.cwd / "src" / "app.py"),
            ))
        return lines

    def _default_calls(self, call: LaunchCall) -> list[tuple[str, dict, bool]]:
        calls = [("Read", {"file_path": str(call.cwd / "src" / "app.py")}, False)]
        if call.agent_name == arms.TOOLKIT.agent_name:
            calls.append(("Read", {"file_path": str(call.config_dir / "agents" / "reviewer.md")}, False))
        return calls

    def _write_session(self, call: LaunchCall, scenario: Scenario) -> None:
        log_lines = _resolve_lines(scenario.log_lines, call)
        if log_lines is None:
            log_lines = self._default_log(call)
        call.log_path.write_text("".join(json.dumps(line) + "\n" for line in log_lines))
        forged_lines = _resolve_lines(scenario.forged_log_lines, call)
        if forged_lines is not None:
            (call.cwd / call.log_path.name).write_text("".join(json.dumps(line) + "\n" for line in forged_lines))
        if scenario.edit is not None:
            scenario.edit(call.cwd)
        else:
            default_edit(call.cwd)
        if scenario.write_account_session_file:
            sessions_dir = call.config_dir / "sessions"
            sessions_dir.mkdir(exist_ok=True)
            (sessions_dir / f"pid-{call.session_id}").write_text(call.session_id + "\n")
        tool_calls = scenario.tool_calls
        if callable(tool_calls):
            tool_calls = tool_calls(call)
        write_transcripts(
            call.config_dir / "projects", call, scenario,
            tool_calls=tool_calls if tool_calls is not None else self._default_calls(call),
            first_turn_size=scenario.first_turn_size if scenario.first_turn_size is not None else (
                TOOLKIT_FIRST_TURN_SIZE if call.agent_name == arms.TOOLKIT.agent_name else BARE_FIRST_TURN_SIZE
            ),
        )
        if scenario.after_write is not None:
            scenario.after_write(call)


def _resolve_lines(lines, call: LaunchCall) -> list[dict] | None:
    """A function of the session ID, or of the session ID and the fixture directory."""
    if not callable(lines):
        return lines
    return lines(call.session_id) if len(inspect.signature(lines).parameters) == 1 else lines(call.session_id, call.cwd)


def default_edit(fixture_dir: Path) -> None:
    (fixture_dir / "src" / "new_module.py").write_text('"""Module doc."""\n# a comment here\nvalue = 1\nother = 2\n')


def write_transcripts(
    projects_root: Path, call: LaunchCall, scenario: Scenario, *, tool_calls: list[tuple[str, dict, bool]],
    first_turn_size: int,
) -> None:
    session_jsonl = projects_root / "synthetic-project" / f"{call.session_id}.jsonl"
    subagent_dir = session_jsonl.parent / call.session_id / "subagents"
    subagent_dir.mkdir(parents=True)
    dispatch = {
        "type": "assistant", "message": {"content": [{
            "type": "tool_use", "id": DISPATCH_TOOL_ID, "name": "Agent",
            "input": {"subagent_type": call.agent_name, "prompt": inner_prompt_from_command(call.cmd)},
        }]},
    }
    session_jsonl.write_text(json.dumps(dispatch) + "\n")
    (subagent_dir / "agent-1.meta.json").write_text(json.dumps(
        {"agentType": call.agent_name, "description": "task", "toolUseId": DISPATCH_TOOL_ID, "spawnDepth": 1},
    ))
    usage = {
        "input_tokens": 10, "cache_creation_input_tokens": 20, "cache_read_input_tokens": first_turn_size - 30,
        "output_tokens": 1,
    }
    entries: list[dict] = []
    for index, (name, tool_input, denied) in enumerate(tool_calls):
        tool_use_id = f"toolu_{index}"
        entries.append({"type": "assistant", "message": {
            "model": scenario.observed_model, "usage": usage,
            "content": [{"type": "tool_use", "id": tool_use_id, "name": name, "input": tool_input}],
        }})
        result_block: dict = {"type": "tool_result", "tool_use_id": tool_use_id, "content": "synthetic"}
        if denied:
            result_block["tool_result_meta"] = [{"non_execution_kind": "permission-rule"}]
        entries.append({"type": "user", "message": {"content": [result_block]}})
    entries.append({"type": "assistant", "message": {
        "model": scenario.observed_model, "usage": usage, "content": [{"type": "text", "text": "src/new_module.py"}],
    }})
    lines = [json.dumps(entry) for entry in entries]
    if scenario.damage_sidecar == "skip":
        lines.insert(len(lines) - 1, "this is not json")
    if scenario.damage_sidecar == "torn":
        lines.append('{"type": "assistant", "message": {"content": [')
    (subagent_dir / "agent-1.jsonl").write_text("\n".join(lines) + "\n")


# --- the world ------------------------------------------------------------------


@dataclass
class ReplayWorld:
    root: Path
    home: Path
    config_dir: Path
    local_dir: Path
    source_repo: Path
    tasks: list[tasks.ReplayTask]
    account: Account

    @property
    def task(self) -> tasks.ReplayTask:
        return self.tasks[0]

    def launch(
        self, scenario_for: Callable[[int, str], Scenario] | None = None,
        by_call: Callable[[LaunchCall], Scenario] | None = None,
    ) -> ScriptedLaunch:
        return ScriptedLaunch(self.config_dir, scenario_for, by_call)


def build_config_dir(config_dir: Path) -> None:
    (config_dir / "rules").mkdir(parents=True)
    (config_dir / "agents").mkdir()
    (config_dir / "skills" / "demo").mkdir(parents=True)
    (config_dir / "CLAUDE.md").write_text(CLAUDE_MD_TEXT)
    (config_dir / "rules" / "python.md").write_text(RULE_TEXT)
    (config_dir / "agents" / "code-writer.md").write_text(CODE_WRITER_MARKDOWN)
    (config_dir / "agents" / "reviewer.md").write_text("reviewer\n")
    (config_dir / "skills" / "demo" / "SKILL.md").write_text("skill\n")
    (config_dir / "settings.json").write_text(json.dumps(SETTINGS_WITH_SESSION_HOOK))
    (config_dir / "sessions").mkdir()


def make_account(config_dir: Path, home: Path) -> Account:
    return Account(
        key=os.path.realpath(config_dir), config_dir=config_dir, config_dir_value=str(config_dir), home=home,
        code_writer_tools=frozenset({"Read", "Edit", "Write", "Bash", "Grep", "Glob"}),
        bare_writer_markdown=arms.bare_writer_markdown(CODE_WRITER_MARKDOWN), dropped_variables=(), probe_seconds=0.0,
    )


def make_ignored_local_dir(root: Path) -> Path:
    """A `.local` directory in a git work tree whose `.gitignore` ignores it, as the checkout's is."""
    checkout = root / "checkout"
    (checkout / "evals" / "replay_bench").mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True, capture_output=True)
    (checkout / ".gitignore").write_text(".local/\n")
    return checkout / "evals" / "replay_bench" / ".local"


def build_replay_world(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, task_count: int = 1, base_files: dict[str, str] | None = None,
) -> ReplayWorld:
    """Under `tmp_path`: a home with an account config dir, a temp dir for fixtures, a git-ignored local dir and a
    source repo with `task_count` qualifying commits (newest first in `tasks`)."""
    home = tmp_path / "home"
    config_dir = home / ".claude"
    build_config_dir(config_dir)
    fixture_root = tmp_path / "tmp"
    fixture_root.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TMPDIR", str(fixture_root))
    monkeypatch.setattr(tempfile, "tempdir", str(fixture_root))
    monkeypatch.setattr(review_runner, "default_live_checkout_roots", lambda: ())
    monkeypatch.setattr(review_runner, "SESSION_FLUSH_POLL_INTERVAL_S", 0.0)
    monkeypatch.setattr(review_runner, "SESSION_FLUSH_TIMEOUT_S", 0.2)
    local_dir = make_ignored_local_dir(tmp_path)
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    repo = _make_repo(source_dir, base_files)
    for index in range(task_count):
        _change(repo, {f"src/mod_{index}.py": _app_with(12)}, message=f"Add module {index}")
    task_list = list(_mine(repo, max_tasks=task_count + 1).tasks)
    monkeypatch.setattr(review_runner, "_worktree_top_levels", lambda checkout: ())
    return ReplayWorld(tmp_path, home, config_dir, local_dir, repo, task_list, make_account(config_dir, home))


def add_second_account(world: ReplayWorld, task_count: int = 1) -> tuple[Account, list[tasks.ReplayTask]]:
    """A second account with its own config dir, and tasks from a second source repo."""
    root = world.root / "second"
    config_dir = root / "home" / ".claude"
    build_config_dir(config_dir)
    source_dir = root / "source"
    source_dir.mkdir()
    repo = _make_repo(source_dir, name="repo-b")
    for index in range(task_count):
        _change(repo, {f"src/other_{index}.py": _app_with(12)}, message=f"Add other {index}")
    task_list = list(_mine(repo, max_tasks=task_count + 1).tasks)
    return make_account(config_dir, world.home), task_list


def make_settings(
    world: ReplayWorld, launch: Callable, *, campaign_id: str = "test", fixture_git_timeout_s: float = 60.0,
    budget_cap_usd: float = 10.0,
) -> attempt.RunSettings:
    campaign_dir = world.local_dir / "campaigns" / campaign_id
    return attempt.RunSettings(
        campaign_id=campaign_id, attempts_root=campaign_dir / "attempts", local_dir=world.local_dir, model_id=MODEL_ID,
        budget_cap_usd=budget_cap_usd, timeout_s=30, fixture_git_timeout_s=fixture_git_timeout_s,
        run_store=RunStore(campaign_dir / "store"),
        spend=SpendFile(campaign_dir / "spend.jsonl", budget_cap_usd=budget_cap_usd),
        runner_environ={"PATH": "/usr/bin", "HOME": str(world.home)}, launch=launch,
    )


def make_block(
    world: ReplayWorld, task: tasks.ReplayTask | None = None, live_checkout_roots: tuple[Path, ...] = (),
) -> attempt.BlockContext:
    return attempt.BlockContext(task or world.task, world.account, READING, live_checkout_roots)


def spend_lines(settings: attempt.RunSettings) -> list[dict]:
    path = settings.spend.path
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def claude_md_line(world: ReplayWorld, session_id: str, config_dir: Path | None = None) -> dict:
    return _line(str((config_dir or world.config_dir) / "CLAUDE.md"), session_id=session_id)


def leaking_log_lines(world: ReplayWorld):
    """A log for a `bare` run that leaks: the dispatcher's `CLAUDE.md` load, then a subagent rule load."""
    return lambda session_id: [
        claude_md_line(world, session_id),
        _line(str(world.config_dir / "rules" / "python.md"), "path_glob_match", agent_id=AGENT_ID, session_id=session_id),
    ]


def validity_of(world: ReplayWorld, call: LaunchCall, cost: float | None = 0.5) -> review_runner.RunValidity:
    """What `evaluate_run_validity` says about the session a scripted launch just wrote."""
    session_jsonl = world.config_dir / "projects" / "synthetic-project" / f"{call.session_id}.jsonl"
    own_session_paths = review_runner.own_session_paths_for(session_jsonl)
    return review_runner.evaluate_run_validity(
        dispatcher_session_jsonl=session_jsonl, stream_lines=stream_lines(cost), timed_out=False,
        expected_agent_name=call.agent_name, expected_inner_prompt=call.inner_prompt, expected_model_id=MODEL_ID,
        agent_declared_tools=world.account.code_writer_tools, fixture_dir=call.cwd,
        own_dirs=(call.cwd, *own_session_paths), projects_root=world.config_dir / "projects",
        own_session_paths=own_session_paths, live_checkout_roots=(), changed_relpaths=(),
    )
