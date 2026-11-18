"""The world the cursor-adapter.py install tests run in, and the helpers their files share.

Every test runs the adapter under a temporary home and PATH, in-process, with the tools a world links into its own bin
directory. Real processes start only for the probes, the self-check and the sessionStart entry, and tests that do not concern the
self-check stub it."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import stat
import sys
import types
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from helpers import CLAUDE_DIR, ambient_umask

sys.path.insert(0, str(CLAUDE_DIR))
from hooks.tests._cursor_payloads import (  # noqa: E402 (the import needs the sys.path entry above)
    ADAPTER_DIR,
    load_adapter,
    load_json,
    symlink_bin_dir,
    wrapper_name,
    write_sh_script,
)

RECORDED_AFTER_PYTHON = ("bash", "jq", "git", "ps", "sha256sum")


@dataclass
class Result:
    status: int
    out: str
    err: str


@dataclass
class World:
    tmp_path: Path
    home: Path
    config_dir: Path
    bin_dir: Path
    adapter: types.ModuleType

    @property
    def cursor_dir(self) -> Path:
        return self.home / ".cursor"

    @property
    def record_dir(self) -> Path:
        return self.cursor_dir / "cursor-adapter"

    @property
    def hooks_path(self) -> Path:
        return self.cursor_dir / "hooks.json"

    @property
    def state_dir(self) -> Path:
        return self.config_dir / "cursor-adapter-state"

    def run(self, *argv: str) -> Result:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = self.adapter.main(list(argv))
        return Result(status, out.getvalue(), err.getvalue())

    def install(self, *extra: str) -> Result:
        return self.run("install", "--config-dir", str(self.config_dir), "--acceptance-only", *extra)

    def snapshot(self) -> dict[str, tuple[bytes | None, int]]:
        """Every file and directory under the home and the config dir, with its bytes and mode."""
        seen: dict[str, tuple[bytes | None, int]] = {}
        for root in (self.cursor_dir, self.config_dir):
            for path in sorted([root, *root.rglob("*")]):
                if path.is_symlink():
                    seen[str(path)] = (os.readlink(path).encode(), 0)
                elif path.is_dir():
                    seen[str(path)] = (None, stat.S_IMODE(path.stat().st_mode))
                else:
                    seen[str(path)] = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        return seen

    def install_record(self) -> dict:
        return load_json(self.record_dir / "install.json")


def make_tool_bin(tmp_path: Path) -> Path:
    """A bin directory holding python3 (the base interpreter, so it is no virtual environment) and every other recorded tool."""
    directory = symlink_bin_dir(tmp_path, *RECORDED_AFTER_PYTHON, wrapper_name(), dirname="bin")
    (directory / "python3").symlink_to(os.path.realpath(sys.executable))
    return directory


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    # The installer refuses group- or world-writable targets, so files and directories must not inherit a 0002 umask.
    # The yield stays inside the with so the pin also covers hooks.json and state directories that test bodies create.
    with ambient_umask(0o022):
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        for name in ("scripts", "hooks", "settings.json"):
            (home / ".claude" / name).symlink_to(CLAUDE_DIR / name)
        (home / ".cursor").mkdir()
        config_dir = tmp_path / "config"
        config_dir.mkdir(mode=0o700)
        (config_dir / "settings.json").symlink_to(CLAUDE_DIR / "settings.json")
        bin_dir = make_tool_bin(tmp_path)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("PATH", str(bin_dir))
        yield World(tmp_path, home, config_dir, bin_dir, load_adapter())


@pytest.fixture
def fast_world(world: World, monkeypatch: pytest.MonkeyPatch) -> World:
    """A world whose self-check is a stand-in that passes, for tests about the files `install` writes."""

    def passing_self_check(python_path, wrapper):
        interpreter = dict.fromkeys(world.adapter.SELF_CHECK_INTERPRETER_KEYS, 1)
        return world.adapter.SelfCheckResult({**interpreter, "executable": python_path}, [])

    monkeypatch.setattr(world.adapter, "run_self_check", passing_self_check)
    return world


def replace_tool(world: World, name: str, body: str) -> Path:
    link = world.bin_dir / name
    link.unlink()
    return write_sh_script(link, body)


def load_adapter_copy_with(world: World, file_name: str, content: str) -> None:
    """Replaces `world.adapter` with a copy of the adapter whose neighbouring file `file_name` holds `content`."""
    copy_root = world.tmp_path / "broken" / "claude" / ".claude" / "scripts"
    shutil.copytree(ADAPTER_DIR, copy_root / "cursor-adapter", ignore=shutil.ignore_patterns("__pycache__"))
    (copy_root / "cursor-adapter" / file_name).write_text(content)
    world.adapter = load_adapter(copy_root / "cursor-adapter" / "cursor-adapter.py", "cursor_adapter_broken_neighbour")


# The three reports that carry the tamper comparison. The last one reaches the agent as `additional_context`.
HOOK_ENV_SURFACE = "check --hook-env"
SURFACES = ["status", "check", HOOK_ENV_SURFACE]


def run_surface(world: World, surface: str) -> Result:
    """One report's output. For the sessionStart check it is the `additional_context` text."""
    result = world.run(*surface.split())
    if surface == HOOK_ENV_SURFACE:
        return Result(result.status, json.loads(result.out)["additional_context"], result.err)
    return result


class Crash(BaseException):
    """A kill between two writes. It is no Exception, because a killed process runs no handler for one."""


def crash_at(world: World, monkeypatch: pytest.MonkeyPatch, point: str) -> None:
    def crash(name: str) -> None:
        if name == point:
            raise Crash(name)

    monkeypatch.setattr(world.adapter, "crash_hook", crash)


def stop_crashing(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(world.adapter, "crash_hook", None)


ORIGINAL_HOOKS = b'{"version": 1, "hooks": {"stop": [{"command": "echo mine"}]}, "extra": [1, 2]}\n'


def hooks_document(world: World) -> dict:
    return json.loads(world.hooks_path.read_text())


def save_hooks(world: World, document: dict) -> None:
    world.hooks_path.write_text(json.dumps(document))


def find_entry(world: World, document: dict, label: str) -> tuple[str, int]:
    for event, items in document["hooks"].items():
        for index, item in enumerate(items):
            if world.adapter.ownership(item) == "owned" and world.adapter.label_of(item) == label:
                return event, index
    raise AssertionError(label)


def mutate_remove(document, event, index):
    del document["hooks"][event][index]


def use_settings(world: World, monkeypatch: pytest.MonkeyPatch, document: dict) -> Path:
    settings = world.tmp_path / "shaped-settings.json"
    settings.write_text(json.dumps(document))
    monkeypatch.setattr(world.adapter, "settings_json_path", lambda: str(settings))
    return settings
