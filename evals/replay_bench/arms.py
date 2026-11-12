"""The replay arms and the files they write into a fixture.

`bare` dispatches a fixture-local `bare-writer` with `omitClaudeMd` and a one-line body, in a fixture
whose settings exclude the account's rule files. `toolkit` dispatches the account's production `code-writer`
under the account's full config.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import measure_subagent_model_resolution as msmr

from replay_bench.checks import LoadExpectation
from replay_bench.launch import check_path_characters

BARE_WRITER_NAME = "bare-writer"
BARE_WRITER_DESCRIPTION = "Implements the change the prompt describes."
BARE_WRITER_BODY = "You implement the change the prompt describes, in the current repository."
CODE_WRITER_AGENT_RELPATH = Path("agents") / "code-writer.md"
CLAUDE_MD_EXCLUDES_KEY = "claudeMdExcludes"

_AGENT_FRONTMATTER_EFFORT_RE = re.compile(r"(?m)^effort:\s*(\S+)\s*$")


class AgentConfigError(ValueError):
    """An account agent file that lacks a field `bare-writer` copies."""


def agent_frontmatter_effort(agent_file_text: str) -> str | None:
    block = msmr._frontmatter_block(agent_file_text)
    if block is None:
        return None
    match = _AGENT_FRONTMATTER_EFFORT_RE.search(block)
    return match.group(1) if match else None


def agent_body(agent_file_text: str) -> str:
    """The text after the frontmatter, stripped. A file with no closing frontmatter marker is all body."""
    block = msmr._frontmatter_block(agent_file_text)
    if block is None:
        return agent_file_text.strip()
    after_marker = agent_file_text[3 + len(block):].split("\n", 2)
    return (after_marker[2] if len(after_marker) > 2 else "").strip()


def agent_body_chars(agent_file_text: str) -> int:
    return len(agent_body(agent_file_text))


def bare_writer_markdown(code_writer_text: str) -> str:
    """`bare-writer.md`, copying `tools`, `model` and `effort` from the account's `code-writer.md`.
    Raises AgentConfigError, naming the missing field, so an empty field never reaches the fixture."""
    tools = msmr.agent_frontmatter_tools(code_writer_text)
    model = msmr.agent_frontmatter_model(code_writer_text)
    effort = agent_frontmatter_effort(code_writer_text)
    for field_name, value in (("tools", tools), ("model", model), ("effort", effort)):
        if not value:
            raise AgentConfigError(f"the account's code-writer.md yields no {field_name}")
    return (
        "---\n"
        f"name: {BARE_WRITER_NAME}\n"
        f"description: {BARE_WRITER_DESCRIPTION}\n"
        f"tools: {', '.join(sorted(tools))}\n"
        f"model: {model}\n"
        f"effort: {effort}\n"
        "omitClaudeMd: true\n"
        "---\n\n"
        f"{BARE_WRITER_BODY}\n"
    )


def code_writer_text(config_dir: Path) -> str:
    """The account's file, which the dispatcher resolves, never this checkout's copy."""
    return (config_dir / CODE_WRITER_AGENT_RELPATH).read_text(encoding="utf-8")


def claude_md_excludes(config_dir: Path) -> list[str]:
    """Every `.md` file under the account's `rules/` at any depth, as the walk reached it and as its realpath.

    The walk follows directory links and skips a directory whose realpath it already visited. Raises
    UnsafeConfigPathError, naming the path, for one that `check_path_characters` refuses."""
    entries: dict[str, None] = {}
    visited: set[str] = set()
    for current, directory_names, file_names in os.walk(config_dir / "rules", followlinks=True):
        real_current = os.path.realpath(current)
        if real_current in visited:
            directory_names[:] = []
            continue
        visited.add(real_current)
        directory_names.sort()
        for file_name in sorted(file_names):
            if file_name.endswith(".md"):
                path = os.path.join(current, file_name)
                for form in (path, os.path.realpath(path)):
                    check_path_characters(form)
                    entries[form] = None
    return list(entries)


def bare_settings_text(config_dir: Path) -> str:
    return json.dumps({CLAUDE_MD_EXCLUDES_KEY: claude_md_excludes(config_dir)})


@dataclass(frozen=True)
class Arm:
    """`excludes_toolkit` arms fail on any subagent load outside the fixture and on any dispatcher load but
    the account's `CLAUDE.md`. `fixture_settings_text` is None for an arm that writes no settings file."""

    name: str
    agent_name: str
    excludes_toolkit: bool
    fixture_settings_text: Callable[[Path], str] | None = None
    allowed_fixture_loads: frozenset[str] = frozenset()

    def load_expectation(self, claude_md: Path) -> LoadExpectation:
        return LoadExpectation(
            expected_dispatcher_claude_md=claude_md, excludes_toolkit=self.excludes_toolkit,
            allowed_fixture_loads=self.allowed_fixture_loads,
        )

    def settings_text(self, config_dir: Path) -> str | None:
        return None if self.fixture_settings_text is None else self.fixture_settings_text(config_dir)


BARE = Arm("bare", BARE_WRITER_NAME, excludes_toolkit=True, fixture_settings_text=bare_settings_text)
TOOLKIT = Arm("toolkit", "code-writer", excludes_toolkit=False)
ARMS: dict[str, Arm] = {arm.name: arm for arm in (TOOLKIT, BARE)}
DEFAULT_ARM_NAMES = tuple(ARMS)
