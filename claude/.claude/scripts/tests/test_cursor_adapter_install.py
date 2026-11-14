"""cursor-adapter.py install, uninstall, check and status.

Every test runs the adapter under a temporary home and PATH, in-process, with the tools a world links into its own bin
directory. Real processes start only for the probes and the self-check, and tests that do not concern the self-check stub it."""

from __future__ import annotations

import contextlib
import errno
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import threading
import types
from dataclasses import dataclass
from pathlib import Path

import pytest
from helpers import CLAUDE_DIR

sys.path.insert(0, str(CLAUDE_DIR))
from hooks.tests._cursor_payloads import (  # noqa: E402 (the import needs the sys.path entry above)
    ADAPTER_DIR,
    ADAPTER_SOURCE,
    CONVERSATION_ID,
    IS_ROOT,
    load_adapter,
    load_json,
    load_shim,
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
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
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
    return World(tmp_path, home, config_dir, bin_dir, load_adapter())


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
TAMPER_PHRASES = (
    "missing or altered entry",
    "owned entry differs",
    "owned entry with no emitted counterpart",
    "duplicated owned entry",
    "foreign entry that holds",
    "non-owned entr",
    "top-level",
)
# An event key with terminal escapes, a bidi override, a line break and 64 KiB of padding, none of which may reach a report.
HOSTILE_EVENT_KEY = "\x1b[31m\x85‮\nHOSTILEKEY" + "A" * 65_536
HOSTILE_EVENT_KEY_FRAGMENTS = ("HOSTILEKEY", "\x1b", "\x85", "‮", "A" * 100)


def run_surface(world: World, surface: str) -> Result:
    """One report's output. For the sessionStart check it is the `additional_context` text."""
    result = world.run(*surface.split())
    if surface == HOOK_ENV_SURFACE:
        return Result(result.status, json.loads(result.out)["additional_context"], result.err)
    return result


def expected_tamper_lines(case: str, expectation: str, label: str, event: str, index: int) -> list[str]:
    """The lines a report holds for a tamper case on the entry `label` at `event` [`index`], per the case's expectation kind."""
    if expectation == "argument":
        expectation = "foreign" if label == "check" else "changed"
    missing = f"missing or altered entry: {label} on {event}"
    return {
        "missing": [missing],
        "changed": [f"owned entry differs from what generate emits now: {label} on {event}"],
        "foreign": [missing, f"foreign entry that holds an adapter path: {event} [{index}]"],
        "moved": [missing, f"owned entry with no emitted counterpart: {label} on stop"],
        "duplicated": [f"duplicated owned entry: {label} on {event}"],
    }[expectation]


class TestInstallRoundTrip:
    def test_a_first_install_writes_the_record_the_pre_image_the_manifest_and_the_merged_hooks_file(
        self, fast_world: World
    ) -> None:
        world = fast_world
        original = b'{"version": 1, "hooks": {"stop": [{"command": "echo mine"}]}}\n'
        world.hooks_path.write_bytes(original)

        result = world.install()

        assert result.status == 0, result.err
        assert (world.record_dir / "hooks.json.pre-install").read_bytes() == original
        record = world.install_record()
        assert record["routed_config_dir"] == str(world.config_dir)
        assert record["acceptance_only"] is True
        assert record["contract_version"] == world.adapter.load_shim().CONTRACT_VERSION
        assert record["tool_map"] == load_json(ADAPTER_DIR / "tool-map.json")
        assert (world.record_dir / "manifest.json").exists()
        assert world.state_dir.is_dir()
        assert stat.S_IMODE(world.state_dir.stat().st_mode) == 0o700
        hooks = json.loads(world.hooks_path.read_text())
        assert hooks["hooks"]["stop"] == [{"command": "echo mine"}]
        assert hooks["version"] == 1

    def test_an_install_then_an_uninstall_restores_the_original_bytes(self, fast_world: World) -> None:
        world = fast_world
        original = b'{"version": 1, "hooks": {"stop": [{"command": "echo mine"}]}, "extra": true}\n'
        world.hooks_path.write_bytes(original)
        world.install()

        result = world.run("uninstall")

        assert result.status == 0, result.err
        assert world.hooks_path.read_bytes() == original
        assert not world.record_dir.exists()
        assert not world.state_dir.exists()

    def test_an_install_with_no_hooks_file_then_an_uninstall_leaves_no_hooks_file(self, fast_world: World) -> None:
        world = fast_world
        assert not world.hooks_path.exists()
        world.install()
        assert world.hooks_path.exists()

        world.run("uninstall")

        assert not world.hooks_path.exists()
        assert not world.record_dir.exists()

    def test_installing_twice_then_uninstalling_leaves_the_original_bytes(self, fast_world: World) -> None:
        world = fast_world
        original = b'{"version": 1, "hooks": {"stop": [{"command": "echo mine"}]}}\n'
        world.hooks_path.write_bytes(original)
        world.install()
        first = world.hooks_path.read_bytes()

        assert world.install().status == 0
        assert world.hooks_path.read_bytes() == first
        world.run("uninstall")

        assert world.hooks_path.read_bytes() == original

    def test_a_second_install_with_another_config_dir_refuses_and_writes_nothing(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        other = world.tmp_path / "other-config"
        other.mkdir(mode=0o700)
        before = world.snapshot()

        result = world.run("install", "--config-dir", str(other), "--acceptance-only")

        assert result.status == 1
        assert "uninstall" in result.err
        assert world.snapshot() == before

    def test_an_engineer_edit_between_install_and_uninstall_survives_with_only_owned_entries_removed(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.hooks_path.write_bytes(b'{"version": 1, "hooks": {}}\n')
        world.install()
        document = json.loads(world.hooks_path.read_text())
        document["hooks"].setdefault("stop", []).append({"command": "echo added later"})
        world.hooks_path.write_text(json.dumps(document))

        world.run("uninstall")

        assert json.loads(world.hooks_path.read_text()) == {"version": 1, "hooks": {"stop": [{"command": "echo added later"}]}}

    def test_install_record_keys_keep_the_listed_tool_order_and_python3_comes_first(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        tool_paths = world.install_record()["tool_paths"]
        assert list(tool_paths) == ["python3", "bash", "jq", "git", "ps", "sha256sum", wrapper_name()]
        shim = world.adapter.load_shim()
        environment = shim.hook_environment(world.install_record())
        first_directory = environment["PATH"].split(":")[0]
        assert first_directory == os.path.dirname(tool_paths["python3"])

    def test_a_second_install_under_a_newer_contract_version_replaces_the_old_entries_and_uninstall_restores_the_original(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        world.hooks_path.write_bytes(ORIGINAL_HOOKS)
        world.install()
        old_commands = {item["command"] for items in hooks_document(world)["hooks"].values() for item in items}
        shim = world.adapter.load_shim()
        monkeypatch.setattr(shim, "CONTRACT_VERSION", "2")

        result = world.install()

        assert result.status == 0, result.err
        assert "--contract-version 2" in world.hooks_path.read_text()
        assert world.install_record()["contract_version"] == "2"
        new_commands = {item["command"] for items in hooks_document(world)["hooks"].values() for item in items}
        assert any("--contract-version 1" in command for command in old_commands)
        assert not any("--contract-version 1" in command for command in new_commands)
        world.run("uninstall")
        assert world.hooks_path.read_bytes() == ORIGINAL_HOOKS

    def test_installing_under_one_python_then_another_then_uninstalling_leaves_the_original_bytes(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        world.hooks_path.write_bytes(ORIGINAL_HOOKS)
        world.install()
        second = world.tmp_path / "second"
        second.mkdir()
        second_bin = make_tool_bin(second)
        monkeypatch.setenv("PATH", str(second_bin))

        assert world.install().status == 0
        text = world.hooks_path.read_text()
        assert str(second_bin / "python3") in text
        assert str(world.bin_dir / "python3") not in text
        world.run("uninstall")

        assert world.hooks_path.read_bytes() == ORIGINAL_HOOKS

    @pytest.mark.parametrize("linked", ["hooks-file", "cursor-directory"])
    def test_a_symlinked_hooks_file_or_cursor_directory_is_refused_by_install_and_uninstall_with_nothing_written(
        self, fast_world: World, linked: str
    ) -> None:
        world = fast_world
        real = world.tmp_path / "dotfiles"
        real.mkdir()
        if linked == "hooks-file":
            (real / "hooks.json").write_bytes(ORIGINAL_HOOKS)
            world.hooks_path.symlink_to(real / "hooks.json")
        else:
            shutil.rmtree(world.cursor_dir)
            world.cursor_dir.symlink_to(real)
        before = (real / "hooks.json").read_bytes() if linked == "hooks-file" else None

        installed = world.install()
        uninstalled = world.run("uninstall")

        assert installed.status == 1
        assert uninstalled.status == 1
        assert "symlink" in installed.err
        assert "symlink" in uninstalled.err
        assert sorted(path.name for path in real.iterdir()) == (["hooks.json"] if linked == "hooks-file" else [])
        if before is not None:
            assert (real / "hooks.json").read_bytes() == before


# --- the tool-path rule ---


class TestInstallerTargets:
    @pytest.mark.parametrize(
        ("kind", "expected_condition"),
        [
            ("relative", 0),
            ("dot-dot", 0),
            ("colon", 1),
            ("newline", 1),
            ("directory", 2),
            ("link-to-directory", 2),
            ("no-execute-bit", 2),
            ("dot-dot-and-colon", 0),
        ],
    )
    def test_a_value_is_refused_under_its_own_condition_and_under_the_first_when_it_fails_two(
        self, world: World, kind: str, expected_condition: int
    ) -> None:
        directory = world.tmp_path / "judged"
        directory.mkdir()
        (directory / "subdirectory").mkdir()
        (directory / "plain").write_text("x")
        (directory / "plain").chmod(0o644)
        link = directory / "link"
        link.symlink_to(directory / "subdirectory")
        values = {
            "relative": "bin/jq",
            "dot-dot": f"{directory}/../judged/plain",
            "colon": f"{directory}/a:b",
            "newline": f"{directory}/a\nb",
            "directory": str(directory / "subdirectory"),
            "link-to-directory": str(link),
            "no-execute-bit": str(directory / "plain"),
            "dot-dot-and-colon": "/x/../y:z",
        }
        assert world.adapter.judge_tool_path(values[kind]) == expected_condition

    def test_an_executable_regular_file_and_a_link_to_one_are_accepted(self, world: World) -> None:
        assert world.adapter.judge_tool_path(str(world.bin_dir / "jq")) is None
        assert world.adapter.judge_tool_path(str(world.bin_dir / "python3")) is None

    @pytest.mark.parametrize(
        ("condition_index", "fix_attribute", "other_fixes"),
        [
            (0, "PATH_FIX_SENTENCE", ("FILE_FIX_SENTENCE", "PYTHON_EXECUTABLE_FIX_SENTENCE")),
            (1, "PATH_FIX_SENTENCE", ("FILE_FIX_SENTENCE", "PYTHON_EXECUTABLE_FIX_SENTENCE")),
            (2, "FILE_FIX_SENTENCE", ("PATH_FIX_SENTENCE", "PYTHON_EXECUTABLE_FIX_SENTENCE")),
        ],
    )
    def test_a_refusal_names_the_tool_the_path_and_the_condition_and_ends_with_only_its_own_fix(
        self, world: World, condition_index: int, fix_attribute: str, other_fixes: tuple[str, ...]
    ) -> None:
        text = str(world.adapter.tool_path_refusal("jq", "/some/jq", condition_index))
        assert "jq" in text
        assert "/some/jq" in text
        assert world.adapter.TOOL_PATH_CONDITION_TEXT[condition_index] in text
        assert text.endswith(getattr(world.adapter, fix_attribute))
        for other in other_fixes:
            assert getattr(world.adapter, other) not in text

    def test_a_tool_reached_through_a_relative_path_entry_is_refused_as_relative(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        working = world.tmp_path / "working"
        (working / "reldir").mkdir(parents=True)
        write_sh_script(working / "reldir" / "jq", "exit 0\n")
        (world.bin_dir / "jq").unlink()
        monkeypatch.chdir(working)
        monkeypatch.setenv("PATH", f"reldir:{world.bin_dir}")
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert "jq" in result.err
        assert world.adapter.TOOL_PATH_CONDITION_TEXT[0] in result.err
        assert world.snapshot() == before

    def test_a_leading_empty_path_element_resolves_to_the_bare_name_and_is_refused_as_relative(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        working = world.tmp_path / "working"
        working.mkdir()
        write_sh_script(working / "jq", "exit 0\n")
        (world.bin_dir / "jq").unlink()
        monkeypatch.chdir(working)
        monkeypatch.setenv("PATH", f":{world.bin_dir}")
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert world.adapter.TOOL_PATH_CONDITION_TEXT[0] in result.err
        assert world.snapshot() == before

    def test_python3s_lookup_runs_inside_the_cursor_directory_so_a_relative_entry_there_is_refused(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        canary = world.tmp_path / "python3-ran"
        (world.cursor_dir / "reldir").mkdir()
        write_sh_script(world.cursor_dir / "reldir" / "python3", f": > {canary}\n")
        monkeypatch.setenv("PATH", f"reldir:{world.bin_dir}")
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert "python3" in result.err
        assert world.adapter.TOOL_PATH_CONDITION_TEXT[0] in result.err
        assert not canary.exists()
        assert world.snapshot() == before

    def test_a_linked_tool_is_recorded_by_the_path_as_found_not_by_its_target(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        assert world.install_record()["tool_paths"]["jq"] == str(world.bin_dir / "jq")
        assert os.path.islink(world.install_record()["tool_paths"]["jq"])

    def test_the_same_file_runs_through_an_accepted_path_so_the_no_run_assertions_can_fail(self, fast_world: World) -> None:
        world = fast_world
        canary = world.tmp_path / "python3-ran"
        (world.bin_dir / "python3").unlink()
        write_sh_script(world.bin_dir / "python3", f": > {canary}\nexit 1\n")

        result = world.install()

        assert result.status == 1
        assert canary.exists()

    @pytest.mark.parametrize("tool_kind", ["bash", "wrapper"])
    def test_a_tool_the_install_runs_is_refused_as_relative_before_it_runs(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch, tool_kind: str
    ) -> None:
        world = fast_world
        tool_name = wrapper_name() if tool_kind == "wrapper" else tool_kind
        working = world.tmp_path / "working"
        (working / "reldir").mkdir(parents=True)
        canary = world.tmp_path / f"{tool_name}-ran"
        write_sh_script(working / "reldir" / tool_name, f": > {canary}\n")
        (world.bin_dir / tool_name).unlink()
        monkeypatch.chdir(working)
        monkeypatch.setenv("PATH", f"reldir:{world.bin_dir}")
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert world.adapter.TOOL_PATH_CONDITION_TEXT[0] in result.err
        assert not canary.exists()
        assert world.snapshot() == before

    @pytest.mark.parametrize("tool_kind", ["bash", "wrapper"])
    def test_the_same_tool_runs_through_an_accepted_path_so_the_relative_row_can_fail(
        self, fast_world: World, tool_kind: str
    ) -> None:
        world = fast_world
        tool_name = wrapper_name() if tool_kind == "wrapper" else tool_kind
        canary = world.tmp_path / f"{tool_name}-ran"
        (world.bin_dir / tool_name).unlink()
        write_sh_script(world.bin_dir / tool_name, f": > {canary}\nexit 1\n")

        result = world.install()

        assert result.status == 1
        assert canary.exists()

    @pytest.mark.parametrize(
        ("template", "condition_index"),
        [
            ("bin/python3", 0),
            ("{base}/x/../python3", 0),
            ("{base}/x/py:thon3", 1),
            ("{base}/x/py\nthon3", 1),
            ("{base}/x/../y:z", 0),
        ],
        ids=["relative", "dot-dot", "colon", "newline", "both-fail"],
    )
    def test_a_sys_executable_failing_either_read_time_condition_is_refused_under_the_first_it_fails(
        self, fast_world: World, template: str, condition_index: int
    ) -> None:
        world = fast_world
        shim = world.adapter.load_shim()
        base = world.tmp_path / "reported"
        (base / "x").mkdir(parents=True)
        (world.cursor_dir / "bin").mkdir()
        reported = template.format(base=base)
        assert shim.is_valid_tool_path(reported) is False
        # An executable sits at the value's own path, so a run before the judgement is observable.
        canary = world.tmp_path / "reported-file-ran"
        write_sh_script(world.cursor_dir / reported if not reported.startswith("/") else Path(reported), f": > {canary}\n")
        (world.bin_dir / "python3").unlink()
        write_sh_script(world.bin_dir / "python3", f"printf '%s\\n' '{json.dumps([reported, '/p', '/p'])}'\n")
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert "python3" in result.err
        assert repr(reported) in result.err
        assert world.adapter.TOOL_PATH_CONDITION_TEXT[condition_index] in result.err
        assert result.err.rstrip().endswith(world.adapter.PYTHON_EXECUTABLE_FIX_SENTENCE)
        assert world.adapter.PATH_FIX_SENTENCE not in result.err
        assert not canary.exists()
        assert world.snapshot() == before

    def test_a_sys_executable_in_another_directory_is_recorded_as_reported_and_that_file_runs(self, fast_world: World) -> None:
        world = fast_world
        other = world.tmp_path / "other-python"
        other.mkdir()
        (other / "python3").symlink_to(os.path.realpath(sys.executable))
        shim = world.adapter.load_shim()
        assert shim.is_valid_tool_path(str(other / "python3")) is True
        (world.bin_dir / "python3").unlink()
        reported = json.dumps([str(other / "python3"), "/p", "/p"])
        floor_program = world.adapter.PROBE_PYTHON_PROGRAM
        write_sh_script(
            world.bin_dir / "python3",
            f"case \"$*\" in *'sys.executable'*) echo '{reported}' ;; *'{floor_program}'*) echo '3 14' ;; esac\n",
        )

        result = world.install()

        assert result.status == 0, result.err
        assert world.install_record()["tool_paths"]["python3"] == str(other / "python3")

    @pytest.mark.parametrize(
        "answer", ["null", "7", '"abc"', "[]", '["/p", "/p"]', '["/p", 1, "/p"]', "{}", "not json"], ids=repr
    )
    def test_a_sys_executable_query_that_does_not_answer_with_three_strings_is_refused_in_one_message(
        self, fast_world: World, answer: str
    ) -> None:
        world = fast_world
        (world.bin_dir / "python3").unlink()
        write_sh_script(world.bin_dir / "python3", f"echo '{answer}'\n")
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert "did not answer the `sys.executable` query" in result.err
        assert "Traceback" not in result.err
        assert world.snapshot() == before

    def test_a_virtual_environment_python_is_refused_naming_the_environment_and_writes_nothing(self, fast_world: World) -> None:
        world = fast_world
        (world.bin_dir / "python3").unlink()
        write_sh_script(world.bin_dir / "python3", 'echo \'["/venv/bin/python3", "/venv", "/base"]\'\n')
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert "virtual environment" in result.err
        assert world.snapshot() == before

    @pytest.mark.parametrize("raw", ["relative/dir", "/has space", "/a$b", "/a;b", "", "/a/../b", "//a", "/a//b", "/a/"])
    def test_a_config_dir_outside_the_allowed_characters_or_with_a_parent_or_empty_component_is_refused(
        self, fast_world: World, raw: str
    ) -> None:
        before = fast_world.snapshot()
        result = fast_world.run("install", "--config-dir", raw, "--acceptance-only")
        assert result.status == 1
        assert "--config-dir" in result.err
        assert fast_world.snapshot() == before

    @pytest.mark.parametrize("home", ["relative", "/h/../x", "/h:x", "/h\nx", ""], ids=repr)
    def test_a_home_the_tool_path_rule_refuses_is_refused_before_any_write(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch, home: str
    ) -> None:
        before = fast_world.snapshot()
        monkeypatch.setenv("HOME", home)

        result = fast_world.install()

        assert result.status == 1
        assert "HOME" in result.err
        assert fast_world.snapshot() == before

    def test_a_config_dir_that_is_a_symlink_or_group_writable_or_inside_a_work_tree_is_refused(self, fast_world: World) -> None:
        world = fast_world
        linked = world.tmp_path / "linked"
        linked.symlink_to(world.config_dir)
        writable = world.tmp_path / "writable"
        writable.mkdir()
        writable.chmod(0o775)
        tree = world.tmp_path / "work-tree"
        (tree / ".git").mkdir(parents=True)
        inside = tree / "config"
        inside.mkdir(mode=0o700)
        for refused in (linked, writable, inside):
            result = world.run("install", "--config-dir", str(refused), "--acceptance-only")
            assert result.status == 1, refused
            assert str(refused) in result.err
        assert not world.record_dir.exists()


# --- prerequisites and the contract ---


class TestPrerequisitesAndContract:
    def _stub_python(self, world: World, version: tuple[int, int]) -> None:
        (world.bin_dir / "python3").unlink()
        reported = json.dumps([str(world.bin_dir / "python3"), "/p", "/p"])
        floor_program = world.adapter.PROBE_PYTHON_PROGRAM
        floor_output = f"{version[0]} {version[1]}"
        write_sh_script(
            world.bin_dir / "python3",
            f"case \"$*\" in *'sys.executable'*) echo '{reported}' ;; *'{floor_program}'*) echo '{floor_output}' ;; esac\n",
        )

    def test_the_refusal_renders_the_adapters_floor(self, world: World) -> None:
        # The constant's own value is tied to `_config.sh` in the hooks tests' floor parity class.
        major, minor = world.adapter.PYTHON_FLOOR
        text = world.adapter.floor_text("python3", "/p", world.adapter.PYTHON_FLOOR, "below", (major, minor - 1))
        assert text.endswith(f"below {major}.{minor} or later.")

    # Each version is derived from the floor, so a floor change moves the rows with it and they keep pinning the boundary.
    @pytest.mark.parametrize(
        "version_for",
        [lambda major, minor: (major, minor - 1), lambda major, minor: (major - 1, 99), lambda major, minor: (major, 9)],
        ids=["one-below", "major-below", "string-order"],
    )
    def test_a_recorded_python_below_the_floor_is_refused_with_its_path_and_version_and_nothing_is_written(
        self, fast_world: World, version_for
    ) -> None:
        world = fast_world
        major, minor = world.adapter.PYTHON_FLOOR
        assert minor > 10, "the string-order row needs a floor minor with two digits"
        version = version_for(major, minor)
        self._stub_python(world, version)
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert str(world.bin_dir / "python3") in result.err
        assert f"{version[0]}.{version[1]}" in result.err
        assert f"{major}.{minor} or later" in result.err
        assert world.snapshot() == before

    @pytest.mark.parametrize(
        "version_for",
        [lambda major, minor: (major, minor + 1), lambda major, minor: (major, 100), lambda major, minor: (major + 1, 0)],
        ids=["one-above", "string-order", "major-above"],
    )
    def test_a_recorded_python_above_the_floor_is_accepted(self, fast_world: World, version_for) -> None:
        world = fast_world
        major, minor = world.adapter.PYTHON_FLOOR
        self._stub_python(world, version_for(major, minor))
        result = world.install()
        assert result.status == 0, result.err

    @pytest.mark.parametrize("output", ["", "garbage", "3", "3 14 1", "a b", "1234567890 1"], ids=repr)
    def test_a_python_probe_without_exactly_two_digit_fields_gives_the_no_version_refusal(
        self, fast_world: World, output: str
    ) -> None:
        world = fast_world
        (world.bin_dir / "python3").unlink()
        reported = json.dumps([str(world.bin_dir / "python3"), "/p", "/p"])
        floor_program = world.adapter.PROBE_PYTHON_PROGRAM
        write_sh_script(
            world.bin_dir / "python3",
            f"case \"$*\" in *'sys.executable'*) echo '{reported}' ;; *'{floor_program}'*) printf '%s' '{output}' ;; esac\n",
        )
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert "printed no version" in result.err
        assert world.snapshot() == before

    def test_the_bare_name_python_in_another_directory_below_the_floor_is_refused_with_that_arm(self, fast_world: World) -> None:
        world = fast_world
        real = world.tmp_path / "real-python"
        real.mkdir()
        (real / "python3-real").symlink_to(os.path.realpath(sys.executable))
        (world.bin_dir / "python3").unlink()
        reported = json.dumps([str(real / "python3-real"), "/p", "/p"])
        floor_program = world.adapter.PROBE_PYTHON_PROGRAM
        write_sh_script(
            world.bin_dir / "python3",
            f"case \"$*\" in *'sys.executable'*) echo '{reported}' ;; *'{floor_program}'*) echo '3 13' ;; esac\n",
        )
        before = world.snapshot()
        # The recorded interpreter lives in `real`, which holds no file named python3, so the hooks run the next one.

        result = world.install()

        assert result.status == 1
        assert str(real) in result.err
        assert "holds none" in result.err
        assert world.snapshot() == before

    def test_the_bare_name_python_sharing_the_recorded_directory_names_the_first_fix(self, fast_world: World) -> None:
        world = fast_world
        directory = world.tmp_path / "shared-python"
        directory.mkdir()
        (directory / "python3-real").symlink_to(os.path.realpath(sys.executable))
        reported = json.dumps([str(directory / "python3-real"), "/p", "/p"])
        floor_program = world.adapter.PROBE_PYTHON_PROGRAM
        write_sh_script(
            directory / "python3",
            f"case \"$*\" in *'sys.executable'*) echo '{reported}' ;; *'{floor_program}'*) echo '3 13' ;; esac\n",
        )
        (world.bin_dir / "python3").unlink()
        (world.bin_dir / "python3").symlink_to(directory / "python3")

        result = world.install()

        assert result.status == 1
        assert f"Make {directory}'s `python3` 3.14 or later" in result.err
        assert "holds none" not in result.err

    @pytest.mark.parametrize(
        "first_line",
        ["BusyBox v1.36.1 multi-call binary.", "", "timeout (some coreutils) 1.0", "timeout (GNU) 9"],
        ids=["busybox", "empty", "coreutils-without-gnu", "gnu-without-coreutils"],
    )
    def test_a_wrapper_that_does_not_name_gnu_coreutils_gets_the_gnu_refusal_and_nothing_is_written(
        self, fast_world: World, first_line: str
    ) -> None:
        world = fast_world
        replace_tool(world, wrapper_name(), f"echo '{first_line}'\n")
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert "GNU coreutils" in result.err
        assert "stricter than the README's `timeout` requirement" in result.err
        assert "brew install coreutils" in result.err
        assert str(world.bin_dir / wrapper_name()) in result.err
        assert world.snapshot() == before

    def _stub_bash(self, world: World, probe_output: str) -> None:
        replace_tool(world, "bash", f"printf '%s\\n' '{probe_output}'\n")

    # Each version is derived from the floor, as in the python rows above.
    @pytest.mark.parametrize(
        "version_for",
        [lambda major, minor: (major, minor - 1), lambda major, minor: (major - 1, 99)],
        ids=["one-below", "major-below"],
    )
    def test_a_recorded_bash_below_the_floor_is_refused_with_its_path_and_version_and_nothing_is_written(
        self, fast_world: World, version_for
    ) -> None:
        world = fast_world
        major, minor = world.adapter.BASH_FLOOR
        version = version_for(major, minor)
        self._stub_bash(world, f"{version[0]} {version[1]}")
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert f"bash at {world.bin_dir / 'bash'} reports {version[0]}.{version[1]}, below {major}.{minor} or later" in result.err
        assert f"Put a bash {major}.{minor} or later first on PATH, then re-run `install`." in result.err
        assert world.snapshot() == before

    @pytest.mark.parametrize(
        "version_for",
        [
            lambda major, minor: (major, minor),
            lambda major, minor: (major, minor + 1),
            lambda major, minor: (major, 10),
            lambda major, minor: (major + 1, 0),
        ],
        ids=["at", "one-above", "string-order", "major-above"],
    )
    def test_a_recorded_bash_at_or_above_the_floor_is_accepted(self, fast_world: World, version_for) -> None:
        world = fast_world
        major, minor = world.adapter.BASH_FLOOR
        assert minor < 10, "the string-order row needs a floor minor with one digit"
        version = version_for(major, minor)
        self._stub_bash(world, f"{version[0]} {version[1]}")

        result = world.install()

        assert result.status == 0, result.err

    @pytest.mark.parametrize("output", ["", "garbage", "5", "5 2 1", "a b", "1234567890 1"], ids=repr)
    def test_a_bash_probe_without_exactly_two_digit_fields_gives_the_no_version_refusal_and_nothing_is_written(
        self, fast_world: World, output: str
    ) -> None:
        world = fast_world
        self._stub_bash(world, output)
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert f"bash at {world.bin_dir / 'bash'} printed no version it could read" in result.err
        assert world.snapshot() == before

    def test_a_bash_the_hooks_path_resolves_ahead_of_the_recorded_one_is_judged_and_refused_when_below_the_floor(
        self, fast_world: World
    ) -> None:
        world = fast_world
        major, minor = world.adapter.BASH_FLOOR
        shadow = world.tmp_path / "shadow"
        shadow.mkdir()
        (shadow / "python3-real").symlink_to(os.path.realpath(sys.executable))
        write_sh_script(shadow / "bash", f"echo '{major} {minor - 1}'\n")
        # The recorded python3 lives in `shadow`, which holds no file named python3, so the hooks run the stub in `bin`.
        (world.bin_dir / "python3").unlink()
        reported = json.dumps([str(shadow / "python3-real"), "/p", "/p"])
        floor_program = world.adapter.PROBE_PYTHON_PROGRAM
        floor_output = " ".join(str(part) for part in world.adapter.PYTHON_FLOOR)
        write_sh_script(
            world.bin_dir / "python3",
            f"case \"$*\" in *'sys.executable'*) echo '{reported}' ;; *'{floor_program}'*) echo '{floor_output}' ;; esac\n",
        )
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        below = f"reports {major}.{minor - 1}, below {major}.{minor} or later"
        assert f"The `bash` the hooks run at {shadow / 'bash'} {below}" in result.err
        assert world.snapshot() == before

    def test_a_hooks_path_that_resolves_no_bash_gives_the_no_bash_cause_and_one_that_does_gives_none(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        tool_paths = {"python3": str(world.bin_dir / "python3"), "bash": str(world.bin_dir / "bash")}
        assert world.adapter.bare_bash_check(tool_paths, str(world.config_dir)) is None
        real_find = world.adapter.find_tool

        def find_without_bash(name, **keywords):
            return None if name == "bash" else real_find(name, **keywords)

        monkeypatch.setattr(world.adapter, "find_tool", find_without_bash)

        cause = world.adapter.bare_bash_check(tool_paths, str(world.config_dir))

        assert cause is not None
        assert cause.startswith("No `bash` resolves on the PATH the hooks run under.")
        assert f"Put a bash {world.adapter.format_version(world.adapter.BASH_FLOOR)} or later first on PATH" in cause

    @pytest.mark.parametrize("tool", ["python3", *RECORDED_AFTER_PYTHON])
    def test_a_recorded_tool_absent_from_path_is_refused_by_name_and_nothing_is_written(
        self, fast_world: World, tool: str
    ) -> None:
        world = fast_world
        (world.bin_dir / tool).unlink()
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert f"`{tool}` is not on PATH." in result.err
        assert "re-run `install`" in result.err
        assert world.snapshot() == before

    def test_neither_wrapper_on_path_gives_the_gnu_refusal_naming_both_as_not_found(self, fast_world: World) -> None:
        world = fast_world
        (world.bin_dir / wrapper_name()).unlink()
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert "Neither `timeout` nor `gtimeout` names GNU coreutils" in result.err
        assert result.err.count(": not found") == 2
        assert world.snapshot() == before

    @pytest.mark.parametrize("surface", ["check", HOOK_ENV_SURFACE])
    @pytest.mark.parametrize("tool", ["python3", "bash", "jq", "sha256sum"])
    def test_a_report_names_a_tool_the_hooks_path_resolves_no_longer_and_names_none_before_it_vanishes(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch, surface: str, tool: str
    ) -> None:
        world = fast_world
        world.install()
        finding = f"No `{tool}` resolves on the PATH the hooks run under."
        assert finding not in run_surface(world, surface).out
        real_find = world.adapter.find_tool

        def find_without_the_tool_on_the_hooks_path(name, **keywords):
            return None if name == tool and keywords.get("search_path") is not None else real_find(name, **keywords)

        monkeypatch.setattr(world.adapter, "find_tool", find_without_the_tool_on_the_hooks_path)

        result = run_surface(world, surface)

        assert finding in result.out
        assert result.status == (1 if surface == "check" else 0)

    def test_a_wrapper_naming_gnu_coreutils_is_accepted_on_that_line(self, fast_world: World) -> None:
        world = fast_world
        replace_tool(world, wrapper_name(), "echo 'timeout (GNU coreutils) 9.4'\n")
        assert world.install().status == 0

    def test_a_wrapper_that_names_gnu_but_does_not_run_its_child_fails_the_self_check_with_the_child_failure_text(
        self, world: World
    ) -> None:
        replace_tool(world, wrapper_name(), "case \"$1\" in --version) echo 'timeout (GNU coreutils) 9.4' ;; *) exit 0 ;; esac\n")
        before = world.snapshot()

        result = world.install()

        wrapper = str(world.bin_dir / wrapper_name())
        assert result.status == 1
        assert f"The wrapper {wrapper} ran with status 0, needed status 3" in result.err
        assert "does not run hooks as the shim needs" in result.err
        assert "hook-run" not in result.err
        assert "GNU coreutils' `timeout`" not in result.err
        assert world.snapshot() == before

    def _copy_scripts_to_stow(self, world: World) -> Path:
        scripts = world.home / ".claude" / "scripts"
        scripts.unlink()
        shutil.copytree(CLAUDE_DIR / "scripts", scripts, ignore=shutil.ignore_patterns("__pycache__", "tests"))
        return scripts / "cursor-adapter"

    def test_a_self_check_that_fails_through_a_real_interpreter_names_the_recorded_path_flags_and_check_and_class(
        self, world: World
    ) -> None:
        (self._copy_scripts_to_stow(world) / "field-table.json").write_text("{not json")
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert str(world.bin_dir / "python3") in result.err
        assert "isolated=1" in result.err
        assert "no_site=1" in result.err
        assert "field-table: ValueError" in result.err
        assert world.snapshot() == before

    def test_a_failure_string_outside_the_grammar_is_rendered_as_other(self, world: World) -> None:
        for failure in (
            "nonsense",
            "hook-run: Refusal not-an-outcome",
            "hook-run: a b c d",
            "unknown-check: Error",
            "hook-run: Refusal sig nal",
        ):
            assert world.adapter.render_failure(failure, "/w") == "self-check: other", failure

    @pytest.mark.parametrize(
        "adapter_source",
        [
            None,
            "",
            "def broken(:\n",
            "import json\nprint(json.dumps({'interpreter': {}, 'failures': ['x: Y']}))\n",
            "import json, sys\nprint(json.dumps({'interpreter': {'executable': 1, 'version': 1, 'isolated': 1, 'no_site': 1}, "
            "'failures': []}))\nsys.exit(1)\n",
        ],
        ids=["removed", "empty", "does-not-compile", "no-interpreter-report", "nonzero-with-a-well-formed-document"],
    )
    def test_a_self_check_run_that_gives_no_result_refuses_install_naming_the_adapter_path(
        self, world: World, adapter_source: str | None
    ) -> None:
        stow_adapter = self._copy_scripts_to_stow(world) / "cursor-adapter.py"
        if adapter_source is None:
            stow_adapter.unlink()
        else:
            stow_adapter.write_text(adapter_source)
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert str(stow_adapter) in result.err
        assert world.snapshot() == before

    @pytest.mark.parametrize(
        ("failure", "expected_parts", "fix_kind"),
        [
            ("hook-run: SelfCheckMismatch status 0", ("ran with status 0, needed status 3",), "replace"),
            ("hook-run: SelfCheckMismatch streams", ("gave status 3 but its child's stdout or stderr",), "replace"),
            ("hook-run: Refusal start-failed ENOENT", ("could not be started at that path", "ENOENT"), "replace"),
            ("hook-run: Refusal start-failed EAGAIN", ("could not be started at that path", "EAGAIN"), "rerun"),
            ("hook-run: Refusal start-failed status:125", ("started and failed on its own", "status:125"), "rerun"),
            ("hook-run: Refusal timeout", ("did not finish within the self-check's deadline", "2 s"), "rerun"),
            ("hook-run: Refusal signal SIGTERM", ("ended by the signal SIGTERM",), "rerun"),
            ("hook-run: Refusal hook-output-cap", ("passed more output than the shim's cap",), "replace"),
        ],
        ids=lambda value: value if isinstance(value, str) and value.startswith("hook-run") else None,
    )
    def test_the_child_failure_text_has_its_outcomes_sentence_and_the_fix_the_split_gives_it(
        self, world: World, failure: str, expected_parts: tuple[str, ...], fix_kind: str
    ) -> None:
        text = world.adapter.child_failure_text("/w/timeout", failure)

        assert text is not None
        assert "/w/timeout" in text
        for part in expected_parts:
            assert part in text
        replace, rerun = world.adapter.REPLACE_WRAPPER_SENTENCE, world.adapter.RERUN_WRAPPER_SENTENCE
        assert text.endswith(replace if fix_kind == "replace" else rerun)
        assert (rerun in text) is (fix_kind == "rerun")

    def test_the_child_failure_text_gives_nothing_for_an_unenterable_directory_and_for_an_unnamed_failure(
        self, world: World
    ) -> None:
        assert world.adapter.child_failure_text("/w", "hook-run: Refusal unenterable-dir") is None
        assert world.adapter.child_failure_text("/w", "hook-run: RuntimeError") is None
        assert world.adapter.child_failure_text("/w", "translation: RuntimeError") is None

    def test_the_child_failure_texts_vocabulary_comes_from_the_shim(self, world: World) -> None:
        shim = world.adapter.load_shim()
        for outcome in shim.HOOK_RUN_OUTCOMES:
            failure = f"hook-run: Refusal {outcome}" + (" ENOENT" if outcome == "start-failed" else "")
            assert (world.adapter.child_failure_text("/w", failure) is None) is (outcome == "unenterable-dir"), outcome

    @pytest.mark.parametrize(
        ("version_output", "expect_in_context"),
        [("3 13", True), ("3 15", False), ("3 14", False), ("nonsense", True)],
        ids=["below", "above", "at", "unreadable"],
    )
    def test_the_hook_env_check_reports_a_bare_name_python_below_the_floor_or_unreadable_and_nothing_at_or_above_it(
        self, fast_world: World, version_output: str, expect_in_context: bool
    ) -> None:
        world = fast_world
        world.install()
        real = world.tmp_path / "real-python"
        real.mkdir()
        (real / "python3.real").symlink_to(os.path.realpath(sys.executable))
        record = world.install_record()
        others = {name: path for name, path in record["tool_paths"].items() if name != "python3"}
        record["tool_paths"] = {"python3": str(real / "python3.real"), **others}
        (world.record_dir / "install.json").write_text(json.dumps(record))
        floor_program = world.adapter.PROBE_PYTHON_PROGRAM
        (world.bin_dir / "python3").unlink()
        write_sh_script(world.bin_dir / "python3", f"case \"$*\" in *'{floor_program}'*) echo '{version_output}' ;; esac\n")

        result = world.run("check", "--hook-env")

        assert result.status == 0
        context = json.loads(result.out)["additional_context"]
        assert (f"`python3` the hooks run at {world.bin_dir / 'python3'}" in context) is expect_in_context


# --- probes ---


class TestProbeBound:
    def _recording_run(self, world: World, monkeypatch: pytest.MonkeyPatch, stalled) -> list[dict]:
        real_run = subprocess.run
        calls: list[dict] = []

        def run(argv, **keywords):
            calls.append({"argv": list(argv), **keywords})
            if stalled(list(argv)):
                raise subprocess.TimeoutExpired(argv, keywords["timeout"])
            return real_run(argv, **keywords)

        monkeypatch.setattr(world.adapter.subprocess, "run", run)
        return calls

    def test_every_probe_goes_out_with_null_stdin_the_bound_and_the_cursor_directory(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        calls = self._recording_run(world, monkeypatch, lambda argv: False)

        assert world.install().status == 0

        assert calls
        for call in calls:
            assert call["stdin"] == subprocess.DEVNULL, call["argv"]
            assert call["stderr"] == subprocess.DEVNULL, call["argv"]
            assert call["timeout"] == world.adapter.PROBE_BOUND_SECONDS, call["argv"]
            assert call["cwd"] == str(world.cursor_dir), call["argv"]

    def test_a_first_install_on_a_host_with_no_cursor_directory_probes_from_home_and_makes_private_files_that_uninstall_removes(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        shutil.rmtree(world.cursor_dir)
        calls = self._recording_run(world, monkeypatch, lambda argv: False)

        result = world.install()

        assert result.status == 0, result.err
        assert calls
        for call in calls:
            assert call["cwd"] == str(world.home), call["argv"]
        assert stat.S_IMODE(world.cursor_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(world.hooks_path.stat().st_mode) == 0o600
        assert world.run("uninstall").status == 0
        assert not world.record_dir.exists()
        assert not world.hooks_path.exists()

    def test_the_session_start_checks_worst_case_probe_time_stays_under_its_entry_timeout_with_headroom(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        world.install()
        calls = self._recording_run(world, monkeypatch, lambda argv: False)
        run_surface(world, HOOK_ENV_SURFACE)
        entry = next(item for item in world.adapter.generate_entries(python_path="/p") if item.label == "check")
        shim = world.adapter.load_shim()
        # A project choice: what the tamper comparison and the interpreter start may take beyond the stdin read and the probes.
        headroom_seconds = 1

        worst_case = shim.MIN_DEADLINE_SECONDS + len(calls) * world.adapter.PROBE_BOUND_SECONDS

        assert calls, "the sessionStart check ran no probe, so the count below would pass vacuously"
        assert entry.entry["timeout"] == world.adapter.CHECK_ENTRY_TIMEOUT_SECONDS
        assert worst_case + headroom_seconds <= world.adapter.CHECK_ENTRY_TIMEOUT_SECONDS

    def test_a_python_floor_probe_uses_the_isolated_c_form_and_the_bare_name_probe_gets_the_hooks_environment(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        calls = self._recording_run(world, monkeypatch, lambda argv: False)

        world.install()

        python_calls = [call for call in calls if call["argv"][1:4] == ["-I", "-S", "-c"]]
        assert {call["argv"][4] for call in python_calls} == {
            world.adapter.QUERY_PYTHON_PROGRAM,
            world.adapter.PROBE_PYTHON_PROGRAM,
        }
        expected_path = world.adapter.load_shim().hook_environment(world.install_record())["PATH"]
        bare = [
            call
            for call in python_calls
            if call["argv"][4] == world.adapter.PROBE_PYTHON_PROGRAM and call["env"]["PATH"] == expected_path
        ]
        assert bare
        assert all(call["env"]["CLAUDE_CONFIG_DIR"] == str(world.config_dir) for call in bare)

    @pytest.mark.parametrize(
        ("tool", "marker"),
        [
            ("python3", "sys.executable"),
            ("python3", "sys.version_info"),
            ("bash", "BASH_VERSINFO"),
            (None, "--version"),
        ],
        ids=["python-query", "python-floor", "bash-floor", "wrapper-version"],
    )
    def test_a_stalled_probe_is_reported_by_the_stall_text_and_install_writes_nothing(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch, tool: str | None, marker: str
    ) -> None:
        world = fast_world
        path = str(world.bin_dir / (tool or wrapper_name()))
        before = world.snapshot()
        self._recording_run(world, monkeypatch, lambda argv: argv[0] == path and any(marker in word for word in argv))

        result = world.install()

        assert result.status == 1
        assert f"{path} did not answer within {world.adapter.PROBE_BOUND_SECONDS} s" in result.err
        assert "`install`" in result.err
        assert "from your own terminal" in result.err
        assert "GNU coreutils" not in result.err
        assert world.snapshot() == before

    @pytest.mark.parametrize("surface", SURFACES)
    def test_a_report_starts_only_its_own_probes_each_with_null_stdin_the_bound_the_cursor_directory_and_an_environment(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch, surface: str
    ) -> None:
        world = fast_world
        world.install()
        calls = self._recording_run(world, monkeypatch, lambda argv: False)

        run_surface(world, surface)

        probed = {(os.path.basename(call["argv"][0]), call["argv"][-1]) for call in calls}
        expected = (
            set()
            if surface == "status"
            else {
                ("python3", world.adapter.PROBE_PYTHON_PROGRAM),
                ("bash", world.adapter.PROBE_BASH_PROGRAM),
                (wrapper_name(), "--version"),
            }
        )
        assert probed == expected, surface
        for call in calls:
            assert call["stdin"] == subprocess.DEVNULL, call["argv"]
            assert call["timeout"] == world.adapter.PROBE_BOUND_SECONDS, call["argv"]
            assert call["cwd"] == str(world.cursor_dir), call["argv"]
        hooks_path = world.adapter.load_shim().hook_environment(world.install_record())["PATH"]
        bare_probes = [call for call in calls if call["env"].get("PATH") == hooks_path]
        assert len(bare_probes) == (0 if surface == "status" else 2), surface
        python_probes = [call for call in calls if call["argv"][-1] == world.adapter.PROBE_PYTHON_PROGRAM]
        assert len(python_probes) == {"status": 0, "check": 2, HOOK_ENV_SURFACE: 1}[surface]

    @pytest.mark.parametrize("surface", ["check", HOOK_ENV_SURFACE])
    @pytest.mark.parametrize(
        ("tool", "marker"),
        [("python3", "sys.version_info"), ("bash", "BASH_VERSINFO"), (None, "--version")],
        ids=["python-floor", "bash-floor", "wrapper-version"],
    )
    def test_a_stalled_probe_in_a_report_gives_the_stall_text_and_no_floor_or_gnu_text(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch, surface: str, tool: str | None, marker: str
    ) -> None:
        world = fast_world
        world.install()
        path = str(world.bin_dir / (tool or wrapper_name()))
        self._recording_run(world, monkeypatch, lambda argv: argv[0] == path and any(marker in word for word in argv))

        result = run_surface(world, surface)

        assert f"{path} did not answer within {world.adapter.PROBE_BOUND_SECONDS} s" in result.out
        assert "`check`" in result.out
        floor_name = world.adapter.format_version(world.adapter.PYTHON_FLOOR)
        for text in (f"below {floor_name}", "printed no version", "GNU coreutils", "could not be confirmed"):
            assert text not in result.out, (surface, tool, text)

    def test_a_stalled_tool_re_test_leaves_the_tamper_comparison_and_the_log_line_done(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        world.install()
        document = hooks_document(world)
        event, index = find_entry(world, document, "require-code-review")
        mutate_remove(document, event, index)
        save_hooks(world, document)
        path = str(world.bin_dir / "python3")
        self._recording_run(world, monkeypatch, lambda argv: argv[0] == path and "sys.version_info" in argv[-1])

        result = run_surface(world, HOOK_ENV_SURFACE)

        assert f"missing or altered entry: require-code-review on {event}" in result.out
        assert "did not answer within" in result.out
        assert [fields["reg"] for fields in world.adapter.read_log(str(world.state_dir)).lines] == ["check-hook-env"]

    @pytest.mark.parametrize("surface", ["check", HOOK_ENV_SURFACE])
    def test_a_python3_that_prints_on_both_streams_but_no_version_gets_the_no_version_text_and_neither_stream(
        self, fast_world: World, surface: str
    ) -> None:
        world = fast_world
        world.install()
        (world.bin_dir / "python3").unlink()
        write_sh_script(world.bin_dir / "python3", "echo SECRETOUT\necho SECRETERR >&2\n")

        result = run_surface(world, surface)

        assert "printed no version it could read" in result.out
        assert "SECRETOUT" not in result.out + result.err
        assert "SECRETERR" not in result.out + result.err

    @pytest.mark.timing
    def test_a_probe_kill_reaches_a_stalled_tool_that_execs_a_sleep_within_the_bound_plus_a_margin(self, world: World) -> None:
        import time

        stub = write_sh_script(world.tmp_path / "stalls", "exec /bin/sleep 20\n")
        started = time.monotonic()
        with pytest.raises(world.adapter.ProbeStalled):
            world.adapter.run_probe(str(stub), [])
        # A kill that reaches the sleep ends the run near the bound, and the margin covers a loaded runner's scheduling delay.
        assert time.monotonic() - started < world.adapter.PROBE_BOUND_SECONDS + 3

    @pytest.mark.timing
    def test_a_probe_returns_within_the_bound_while_a_child_of_the_stalled_tool_holds_its_output_pipe(self, world: World) -> None:
        import time

        stub = write_sh_script(world.tmp_path / "stalls-with-child", "/bin/sleep 8 &\nexec /bin/sleep 20\n")
        started = time.monotonic()
        with pytest.raises(world.adapter.ProbeStalled):
            world.adapter.run_probe(str(stub), [])
        # The child holds the pipe for 8 s, so a run that waited for it overshoots the bound by more than this margin.
        assert time.monotonic() - started < world.adapter.PROBE_BOUND_SECONDS + 3


# --- the run-mode line ---


RUN_MODE_FILES = {
    "absent": None,
    "unparseable": "{not json",
    "autorun-shape": json.dumps({"autoRun": {"mode": "allowlist", "allowlist": []}}),
    "g7-keys": json.dumps({"permissions": {"allow": ["Shell(ls)"]}, "sandbox": False}),
}


class TestRunModeWarning:
    def test_install_refuses_without_acceptance_only_and_every_tree_stays_byte_identical(self, fast_world: World) -> None:
        world = fast_world
        before = world.snapshot()

        result = world.run("install", "--config-dir", str(world.config_dir))

        assert result.status == 1
        assert "--acceptance-only" in result.err
        for gate in ("require-code-review", "require-plan-review", "require-ready-for-review", "require-respond-pr"):
            assert gate in result.err
        assert "require-memory-skill" in result.err
        assert "add --acceptance-only to run it" in result.err
        assert world.adapter.MARKER_GATES_WARNING not in result.err
        assert world.snapshot() == before
        assert not world.state_dir.exists()

    def test_install_with_the_flag_records_the_acceptance_only_key(self, fast_world: World) -> None:
        assert fast_world.install().status == 0
        assert fast_world.install_record()["acceptance_only"] is True

    @pytest.mark.parametrize("permissions_file", list(RUN_MODE_FILES))
    def test_each_channel_prints_the_run_mode_line_and_the_acceptance_line_whatever_the_permissions_file_holds(
        self, fast_world: World, permissions_file: str
    ) -> None:
        world = fast_world
        content = RUN_MODE_FILES[permissions_file]
        if content is not None:
            (world.cursor_dir / "permissions.json").write_text(content)
        adapter = world.adapter

        installed = world.install()
        checked = world.run("check")
        status = world.run("status")
        context = json.loads(world.run("check", "--hook-env").out)["additional_context"]

        for text in (installed.out, checked.out, status.out, context):
            assert adapter.RUN_MODE_LINE in text
            assert adapter.ACCEPTANCE_ONLY_LINE in text

    def test_the_session_start_log_lines_run_mode_field_holds_the_closed_label(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        world.run("check", "--hook-env")
        lines = world.adapter.read_log(str(world.state_dir)).lines
        assert [fields["runmode"] for fields in lines if fields.get("reg") == "check-hook-env"] == ["unreadable+acceptance-only"]

    def test_a_record_without_the_acceptance_key_prints_no_acceptance_line_and_logs_the_plain_label(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()
        record = world.install_record()
        del record["acceptance_only"]
        (world.record_dir / "install.json").write_text(json.dumps(record))

        context = json.loads(world.run("check", "--hook-env").out)["additional_context"]

        assert world.adapter.ACCEPTANCE_ONLY_LINE not in context
        assert world.adapter.RUN_MODE_LINE in context
        assert world.adapter.read_log(str(world.state_dir)).lines[-1]["runmode"] == "unreadable"


# --- the log's field table ---


class TestLogFieldTable:
    def test_the_check_entrys_line_fits_its_field_and_the_line_stays_under_the_cap(self, fast_world: World) -> None:
        world = fast_world
        shim = world.adapter.load_shim()
        world.install()
        world.run("check", "--hook-env")

        raw = (world.state_dir / "shim.log").read_bytes()
        fields = world.adapter.read_log(str(world.state_dir)).lines[-1]

        assert fields["runmode"] == "unreadable+acceptance-only"
        assert len(fields["runmode"]) <= dict(shim.LOG_FIELDS)["runmode"]
        assert max(len(line) + 1 for line in raw.split(b"\n") if line) < shim.LOG_LINE_MAX_BYTES

    def test_the_overflow_token_holds_only_characters_the_log_grammar_allows(self, world: World) -> None:
        import re

        shim = world.adapter.load_shim()
        assert re.fullmatch(shim.LOG_VALUE_PATTERN, shim.LOG_OVERFLOW_TOKEN)

    def test_the_writer_keeps_a_line_with_every_field_at_its_maximum_under_the_cap(self, world: World) -> None:
        shim = world.adapter.load_shim()
        fields = {name: "a" * maximum for name, maximum in shim.LOG_FIELDS}

        line = shim.format_log_line(fields)

        assert len(line) < shim.LOG_LINE_MAX_BYTES
        assert line.endswith(b"\n")
        assert all(f"{name}={'a' * maximum}".encode() in line for name, maximum in shim.LOG_FIELDS)

    def test_both_run_mode_labels_fit_the_field(self, world: World) -> None:
        shim = world.adapter.load_shim()
        maximum = dict(shim.LOG_FIELDS)["runmode"]
        assert len(world.adapter.RUN_MODE_LABEL) <= maximum
        assert len(world.adapter.RUN_MODE_ACCEPTANCE_LABEL) <= maximum


# --- rotation and the sweep ---


class TestLogRotationAndSweep:
    def _log(self, world: World, size: int) -> Path:
        world.state_dir.mkdir(exist_ok=True)
        log = world.state_dir / "shim.log"
        log.write_bytes(b"x" * size)
        return log

    def test_a_log_past_the_threshold_is_renamed_to_the_one_older_file(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(world.adapter, "LOG_ROTATE_BYTES", 100)
        self._log(world, 101)

        assert world.adapter.rotate_log(str(world.state_dir)) is True

        assert not (world.state_dir / "shim.log").exists()
        assert (world.state_dir / "shim.log.1").read_bytes() == b"x" * 101

    def test_a_rotator_skips_while_another_holds_the_lock_and_the_older_file_keeps_its_content(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import fcntl

        monkeypatch.setattr(world.adapter, "LOG_ROTATE_BYTES", 100)
        log = self._log(world, 500)
        (world.state_dir / "shim.log.1").write_bytes(b"older")
        descriptor = os.open(world.state_dir / "rotate.lock", os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

            assert world.adapter.rotate_log(str(world.state_dir)) is False
        finally:
            os.close(descriptor)

        assert log.read_bytes() == b"x" * 500
        assert (world.state_dir / "shim.log.1").read_bytes() == b"older"

    def test_a_small_already_rotated_log_is_left_alone_by_a_rotator_that_holds_the_lock(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(world.adapter, "LOG_ROTATE_BYTES", 100)
        self._log(world, 10)
        (world.state_dir / "shim.log.1").write_bytes(b"older")

        assert world.adapter.rotate_log(str(world.state_dir)) is False

        assert (world.state_dir / "shim.log.1").read_bytes() == b"older"
        assert (world.state_dir / "shim.log").read_bytes() == b"x" * 10

    def test_a_rotation_under_concurrent_appends_loses_no_line(self, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
        import threading

        shim = world.adapter.load_shim()
        monkeypatch.setattr(world.adapter, "LOG_ROTATE_BYTES", 2000)
        world.state_dir.mkdir()
        total = 400
        started = threading.Event()

        def append_lines() -> None:
            for number in range(total):
                shim.write_log_line(str(world.state_dir), {"ts": number, "reg": "writer", "out": "ok"})
                started.set()

        writer = threading.Thread(target=append_lines)
        writer.start()
        started.wait()
        rotated = False
        while writer.is_alive() and not rotated:
            rotated = world.adapter.rotate_log(str(world.state_dir))
        writer.join()
        # The writer's output exceeds the threshold, so a rotation that the loop missed still lands once it has finished.
        rotated = rotated or world.adapter.rotate_log(str(world.state_dir))
        assert rotated

        names = ("shim.log.1", "shim.log")
        counted = sum(
            len((world.state_dir / name).read_bytes().splitlines()) for name in names if (world.state_dir / name).exists()
        )
        assert counted == total

    def test_a_mark_just_over_seven_days_old_is_removed_one_just_under_is_kept_and_a_non_mark_stays(self, world: World) -> None:
        marks = world.state_dir / "first-call"
        marks.mkdir(parents=True)
        now = 1_800_000_000.0
        age = world.adapter.FIRST_CALL_MARK_MAX_AGE_SECONDS
        for name, mtime in (("old-session", now - age - 5), ("fresh-session", now - age + 5), ("not.a.mark", now - age - 5)):
            (marks / name).write_text("")
            os.utime(marks / name, (mtime, mtime))

        removed = world.adapter.sweep_first_call_marks(str(world.state_dir), now)

        assert removed == 1
        assert sorted(path.name for path in marks.iterdir()) == ["fresh-session", "not.a.mark"]


# --- crash injection ---


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


class TestInstallCrashInjection:
    @pytest.mark.parametrize("original", [ORIGINAL_HOOKS, None], ids=["engineers-file", "no-file"])
    @pytest.mark.parametrize(
        "point", ["after-pre-image", "after-install-record", "after-generated-files", "after-hooks-json", "after-manifest"]
    )
    def test_a_crash_at_each_install_point_then_uninstall_leaves_the_original_bytes_or_no_file(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch, point: str, original: bytes | None
    ) -> None:
        world = fast_world
        if original is not None:
            world.hooks_path.write_bytes(original)
        crash_at(world, monkeypatch, point)
        with pytest.raises(Crash):
            world.install()
        stop_crashing(world, monkeypatch)

        result = world.run("uninstall")

        assert result.status == 0, result.err
        assert (world.hooks_path.read_bytes() if world.hooks_path.exists() else None) == original
        assert not world.record_dir.exists()

    @pytest.mark.parametrize("point", ["after-entries", "after-generated-files", "after-state", "after-record"])
    def test_a_crash_at_each_uninstall_point_then_install_and_uninstall_leaves_the_original_bytes(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch, point: str
    ) -> None:
        world = fast_world
        world.hooks_path.write_bytes(ORIGINAL_HOOKS)
        world.install()
        crash_at(world, monkeypatch, point)
        with pytest.raises(Crash):
            world.run("uninstall")
        stop_crashing(world, monkeypatch)

        assert world.install().status == 0
        assert world.run("uninstall").status == 0

        assert world.hooks_path.read_bytes() == ORIGINAL_HOOKS

    @pytest.mark.parametrize("point", ["after-entries", "after-generated-files", "after-state", "after-record"])
    def test_a_crash_at_each_uninstall_point_then_a_plain_uninstall_finishes_without_a_false_left_in_place_line(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch, point: str
    ) -> None:
        world = fast_world
        world.hooks_path.write_bytes(ORIGINAL_HOOKS)
        world.install()
        crash_at(world, monkeypatch, point)
        with pytest.raises(Crash):
            world.run("uninstall")
        stop_crashing(world, monkeypatch)

        result = world.run("uninstall")

        assert result.status == 0, result.err
        assert "left in place" not in result.out
        assert ("the runtime state may already have been removed" in result.out) is (point == "after-record")
        assert result.out.strip().splitlines()[-1].startswith("Uninstalled.")
        assert world.hooks_path.read_bytes() == ORIGINAL_HOOKS
        assert not world.record_dir.exists()
        assert not world.state_dir.exists()

    def test_an_interrupted_uninstall_then_an_edit_then_install_and_uninstall_leaves_the_edited_bytes(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        world.hooks_path.write_bytes(ORIGINAL_HOOKS)
        world.install()
        crash_at(world, monkeypatch, "after-entries")
        with pytest.raises(Crash):
            world.run("uninstall")
        stop_crashing(world, monkeypatch)
        edited = b'{"version": 1, "hooks": {"stop": [{"command": "echo edited"}]}}\n'
        world.hooks_path.write_bytes(edited)

        world.install()
        world.run("uninstall")

        assert world.hooks_path.read_bytes() == edited

    def test_an_edit_between_the_merge_and_the_rename_aborts_after_writes_with_the_partial_write_status_and_keeps_the_edit(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        world.hooks_path.write_bytes(ORIGINAL_HOOKS)
        edited = b'{"version": 1, "hooks": {"stop": [{"command": "echo edited"}]}}\n'

        def edit_the_file(name: str) -> None:
            if name == "after-generated-files":
                world.hooks_path.write_bytes(edited)

        monkeypatch.setattr(world.adapter, "crash_hook", edit_the_file)

        result = world.install()

        assert result.status == world.adapter.PARTIAL_WRITE_EXIT_STATUS
        assert "failed after writes began" in result.err
        assert "refused:" not in result.err
        assert str(world.hooks_path) in result.err
        assert "after the step after-generated-files" in result.err
        assert (world.record_dir / "install.json").exists()
        assert world.hooks_path.read_bytes() == edited
        stop_crashing(world, monkeypatch)
        assert world.run("uninstall").status == 0


# --- ownership, the print set and uninstall ---


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


# The events the adapter writes entries under, where an engineer's own hooks are the usual case.
EVENTS_SHARED_WITH_THE_ENGINEER = ("preToolUse", "beforeShellExecution", "sessionStart")


class TestEngineerEntriesOnEventsTheAdapterWrites:
    def test_install_keeps_the_engineers_entries_ahead_of_the_emitted_ones_and_a_rewriting_uninstall_leaves_exactly_them(
        self, fast_world: World
    ) -> None:
        world = fast_world
        before_entries = {event: {"command": f"echo before-{event}"} for event in EVENTS_SHARED_WITH_THE_ENGINEER}
        after_entries = {event: {"command": f"echo after-{event}"} for event in EVENTS_SHARED_WITH_THE_ENGINEER}
        save_hooks(world, {"version": 1, "hooks": {event: [entry] for event, entry in before_entries.items()}})
        assert world.install().status == 0
        document = hooks_document(world)
        for event, entry in after_entries.items():
            document["hooks"][event].append(entry)
        save_hooks(world, document)

        assert world.install().status == 0

        installed = hooks_document(world)["hooks"]
        for event in EVENTS_SHARED_WITH_THE_ENGINEER:
            items = installed[event]
            assert items[:2] == [before_entries[event], after_entries[event]], event
            assert len(items) > 2, event
            assert all(world.adapter.ownership(item) == "owned" for item in items[2:]), event
        counted = ", ".join(f"{event} 2" for event in sorted(EVENTS_SHARED_WITH_THE_ENGINEER))
        for surface in SURFACES:
            assert f"non-owned entries by event: {counted}" in run_surface(world, surface).out, surface

        uninstalled = world.run("uninstall")

        assert uninstalled.status == 0, uninstalled.err
        assert "is not restored from the saved pre-install file" in uninstalled.out
        assert hooks_document(world)["hooks"] == {
            event: [before_entries[event], after_entries[event]] for event in EVENTS_SHARED_WITH_THE_ENGINEER
        }


class TestUninstallOwnership:
    def _frozen_check_command(self, world: World, python_path: str) -> str:
        adapter_path = world.adapter.stow_adapter_path()
        return (
            f"out=$({python_path} -I -S {adapter_path} check --hook-env 2>/dev/null); rc=$?; "
            "if [ \"$rc\" -eq 0 ] && [ -n \"$out\" ]; then printf '%s' \"$out\"; exit 0; fi; printf '%s' '{}'; exit 0"
        )

    @pytest.mark.parametrize("python_path", ["/usr/bin/python3", "/opt/py/bin/python3.14"])
    def test_the_frozen_check_entry_literal_parses_as_owned_under_any_recorded_python_path(
        self, world: World, python_path: str
    ) -> None:
        command = self._frozen_check_command(world, python_path)
        parsed = world.adapter.parse_command(command)
        assert parsed is not None
        assert parsed.form == "check"
        assert world.adapter.ownership({"command": command}) == "owned"
        assert command == world.adapter.render_command(
            python_path, world.adapter.stow_adapter_path(), world.adapter.CHECK_ARGUMENTS
        )

    def test_a_python_path_holding_a_space_and_a_quote_round_trips_through_the_grammar(self, world: World) -> None:
        python_path = "/opt/it's mine/bin/python3"
        shim_path = world.adapter.stow_shim_path()
        arguments = ["--claude-event", "PreToolUse", "--label", "x y", "--fail-closed"]
        command = world.adapter.render_command(python_path, shim_path, arguments, fail_closed_event="preToolUse")
        parsed = world.adapter.parse_command(command)
        assert (parsed.python_path, parsed.script_path, parsed.arguments) == (python_path, shim_path, arguments)

    def _foreign_shapes(self, world: World) -> dict[str, str]:
        adapter = world.adapter
        python_path = "/usr/bin/python3"
        adapter_path, shim_path = adapter.stow_adapter_path(), adapter.stow_shim_path()
        check = adapter.render_command(python_path, adapter_path, adapter.CHECK_ARGUMENTS)
        shim_arguments = ["--contract-version", "1", "--claude-event", "PreToolUse", "--mode", "classify", "--fail-closed"]
        closed_shim = adapter.render_command(python_path, shim_path, shim_arguments, fail_closed_event="preToolUse")
        shapes = {
            f"adapter-{name}": adapter.render_command(python_path, adapter_path, [name])
            for name in ("install", "uninstall", "status", "self-check")
        }
        shapes["adapter-bare-check"] = adapter.render_command(python_path, adapter_path, ["check"])
        shapes["adapter-check-with-an-extra-argument"] = adapter.render_command(
            python_path, adapter_path, ["check", "--hook-env", "--x"]
        )
        shapes["adapter-check-under-a-fail-closed-tail"] = adapter.render_command(
            python_path, adapter_path, ["check", "--hook-env"], fail_closed_event="preToolUse"
        )
        shapes["adapter-with-the-shims-arguments"] = adapter.render_command(python_path, adapter_path, shim_arguments)
        shapes["shim-with-check-hook-env"] = adapter.render_command(python_path, shim_path, ["check", "--hook-env"])
        shapes["shim-tail-exit-changed"] = closed_shim.replace("exit 2", "exit 0")
        shapes["check-with-an-or-true-suffix"] = check + " || true"
        shapes["shim-with-an-unknown-flag"] = adapter.render_command(python_path, shim_path, [*shim_arguments, "--unknown"])
        return shapes

    def test_each_foreign_shape_is_named_foreign_printed_and_kept_by_install_and_by_uninstall(self, fast_world: World) -> None:
        world = fast_world
        shapes = self._foreign_shapes(world)
        for name, command in shapes.items():
            assert world.adapter.ownership({"command": command}) == "foreign", name
        world.hooks_path.write_text(
            json.dumps({"version": 1, "hooks": {"stop": [{"command": command} for command in shapes.values()]}})
        )

        installed = world.install()
        document = hooks_document(world)
        uninstalled = world.run("uninstall")

        assert installed.status == 0, installed.err
        assert installed.out.count("foreign entry:") == len(shapes)
        assert [item["command"] for item in document["hooks"]["stop"] if world.adapter.ownership(item) == "foreign"] == list(
            shapes.values()
        )
        assert uninstalled.out.count("foreign entry:") == len(shapes)
        assert f"{len(shapes)} kept foreign entry(s) still hold an adapter path" in uninstalled.out
        assert world.adapter.FOREIGN_FIX_SENTENCE in uninstalled.out
        assert [item["command"] for item in hooks_document(world)["hooks"]["stop"]] == list(shapes.values())

    def test_an_element_without_a_string_command_is_non_owned_kept_and_printed_when_the_pre_image_lacks_it(
        self, fast_world: World
    ) -> None:
        world = fast_world
        prompt_entry = {"type": "prompt", "prompt": "be careful"}
        world.install()
        document = hooks_document(world)
        document["hooks"].setdefault("stop", []).append(prompt_entry)
        save_hooks(world, document)
        assert world.adapter.ownership(prompt_entry) == "non-owned"

        result = world.run("uninstall")

        assert "non-owned entry the pre-install file does not hold" in result.out
        assert prompt_entry in hooks_document(world)["hooks"]["stop"]

    def test_a_non_owned_entry_added_after_every_owned_one_was_removed_is_printed_by_event_and_position(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()
        assert not (world.record_dir / "hooks.json.pre-install").exists()
        world.hooks_path.write_text(json.dumps({"version": 1, "hooks": {"stop": [{"command": "echo new"}]}}))

        result = world.install()

        assert "non-owned entry the pre-install file does not hold: stop [0]" in result.out
        assert (world.record_dir / "hooks.json.pre-install").read_bytes() == (
            b'{"version": 1, "hooks": {"stop": [{"command": "echo new"}]}}'
        )

    def _installed_with_mutation(self, world: World, mutate) -> tuple[Result, dict]:
        world.hooks_path.write_bytes(ORIGINAL_HOOKS)
        world.install()
        document = hooks_document(world)
        mutate(document)
        save_hooks(world, document)
        return world.install(), document

    @pytest.mark.parametrize(
        ("name", "mutate"),
        [
            ("matcher", lambda doc: doc["hooks"]["preToolUse"][0].update(matcher="Read")),
            ("failClosed", lambda doc: doc["hooks"]["preToolUse"][0].pop("failClosed")),
            ("timeout", lambda doc: doc["hooks"]["preToolUse"][0].update(timeout=99)),
            ("key-set", lambda doc: doc["hooks"]["preToolUse"][0].update(extra=1)),
            (
                "tail-document",
                lambda doc: doc["hooks"]["preToolUse"][0].update(
                    command=doc["hooks"]["preToolUse"][0]["command"].replace('"permission":"deny"', '"permission":"allow"')
                ),
            ),
            ("duplicate", lambda doc: doc["hooks"]["preToolUse"].append(dict(doc["hooks"]["preToolUse"][0]))),
        ],
    )
    def test_install_prints_an_owned_entry_that_differs_or_repeats_before_replacing_it(
        self, fast_world: World, name: str, mutate
    ) -> None:
        result, document = self._installed_with_mutation(fast_world, mutate)

        assert result.status == 0, result.err
        assert ("duplicated owned entry" if name == "duplicate" else "owned entry that differs") in result.out
        reported_position = len(document["hooks"]["preToolUse"]) - 1 if name == "duplicate" else 0
        assert f"preToolUse [{reported_position}]" in result.out

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda doc: None,
            lambda doc: doc["hooks"]["preToolUse"][0].update(
                command=doc["hooks"]["preToolUse"][0]["command"].replace("--contract-version 1", "--contract-version 0")
            ),
        ],
        ids=["intact", "version-token-only"],
    )
    def test_install_prints_nothing_for_an_intact_file_or_a_version_token_difference(self, fast_world: World, mutate) -> None:
        result, _ = self._installed_with_mutation(fast_world, mutate)
        assert result.status == 0
        assert "entry" not in result.out.split("Installed")[0]

    def test_a_non_owned_entry_the_pre_image_holds_is_not_printed(self, fast_world: World) -> None:
        world = fast_world
        world.hooks_path.write_bytes(ORIGINAL_HOOKS)
        world.install()
        again = world.install()
        uninstalled = world.run("uninstall")
        assert "entry" not in again.out.split("Installed")[0]
        assert "non-owned" not in uninstalled.out

    def test_a_python_path_difference_alone_is_not_printed_by_install(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        document = hooks_document(world)
        for items in document["hooks"].values():
            for item in items:
                parsed = world.adapter.parse_command(item["command"])
                item["command"] = item["command"].replace(parsed.python_path, "/some/other/python3", 1)
        save_hooks(world, document)

        result = world.install()

        assert "entry" not in result.out.split("Installed")[0]
        assert "/some/other/python3" not in world.hooks_path.read_text()

    def test_control_characters_are_escaped_and_a_long_value_is_cut_with_its_full_length(self, fast_world: World) -> None:
        world = fast_world
        hostile = "\x1b[31mred\x85‮gnp\nline"
        command = hostile + " " + world.adapter.stow_shim_path() + " " + "A" * 70_000
        world.hooks_path.write_text(json.dumps({"version": 1, "hooks": {"stop": [{"command": command}]}}))

        result = world.install()

        assert "\x1b" not in result.out
        assert "‮" not in result.out
        assert "\\u001b" in result.out
        assert "\\x85" in result.out
        assert "\\u202e" in result.out
        assert "characters in all]" in result.out
        assert max(len(line) for line in result.out.splitlines()) < 2000

    def test_twenty_one_entries_print_twenty_in_class_order_and_the_rest_as_a_count(self, fast_world: World) -> None:
        world = fast_world
        foreign = {"command": world.adapter.stow_shim_path() + " nonsense"}
        world.hooks_path.write_text(json.dumps({"version": 1, "hooks": {"stop": [dict(foreign, n=i) for i in range(21)]}}))

        result = world.install()

        assert result.out.count("foreign entry:") == world.adapter.PRINT_SET_MAX_ENTRIES
        assert "1 more kept foreign entry(s) not shown" in result.out

    def test_ten_thousand_foreign_entries_print_twenty_and_a_count_of_the_rest(self, fast_world: World) -> None:
        world = fast_world
        foreign = {"command": world.adapter.stow_shim_path() + " nonsense"}
        world.hooks_path.write_text(json.dumps({"version": 1, "hooks": {"stop": [dict(foreign, n=i) for i in range(10_000)]}}))

        result = world.install()

        assert result.out.count("foreign entry:") == 20
        assert "9980 more kept foreign entry(s) not shown" in result.out

    @pytest.mark.parametrize(
        "content",
        [b"{not json", b"[1, 2]", b'{"hooks": []}', b'{"hooks": {"stop": {}}}'],
        ids=["invalid", "array", "hooks-list", "event-not-array"],
    )
    def test_a_hooks_file_the_merge_cannot_use_makes_install_and_uninstall_refuse_with_the_tree_byte_identical(
        self, fast_world: World, content: bytes
    ) -> None:
        world = fast_world
        world.hooks_path.write_bytes(content)
        before = world.snapshot()

        installed = world.install()
        uninstalled = world.run("uninstall")

        assert installed.status == 1
        assert str(world.hooks_path) in installed.err
        assert uninstalled.status == 1
        assert str(world.hooks_path) in uninstalled.err
        assert world.snapshot() == before

    def test_a_missing_hooks_file_is_named_by_uninstall_which_runs_its_other_steps(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        world.hooks_path.unlink()

        result = world.run("uninstall")

        assert result.status == 0
        assert f"{world.hooks_path} does not exist" in result.out
        assert not world.record_dir.exists()

    def test_uninstall_with_the_whole_record_directory_missing_removes_the_entries_and_names_the_state_it_left(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()
        shutil.rmtree(world.record_dir)

        result = world.run("uninstall")

        assert result.status == 0
        assert "runtime state could not be located and was left in place" in result.out
        assert "the runtime state directory remains" in result.out.strip().splitlines()[-1]
        assert world.state_dir.is_dir()
        assert [item for items in hooks_document(world)["hooks"].values() for item in items] == []

    def test_uninstall_with_only_the_install_record_missing_says_the_state_may_be_gone_and_does_not_count_it_as_left(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()
        (world.record_dir / "install.json").unlink()

        result = world.run("uninstall")

        assert result.status == 0
        assert "the runtime state may already have been removed" in result.out
        assert "left in place" not in result.out
        assert result.out.strip().splitlines()[-1].startswith("Uninstalled.")
        assert not world.hooks_path.exists()
        assert not world.record_dir.exists()

    def test_uninstall_with_the_record_malformed_behaves_as_with_it_missing(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        (world.record_dir / "install.json").write_text("{not json")

        result = world.run("uninstall")

        assert "runtime state could not be located" in result.out
        assert world.state_dir.is_dir()

    def test_a_state_directory_that_is_a_symlink_is_named_and_nothing_under_it_is_removed(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        target = world.tmp_path / "elsewhere"
        target.mkdir()
        (target / "keep").write_text("x")
        shutil.rmtree(world.state_dir)
        world.state_dir.symlink_to(target)

        result = world.run("uninstall")

        assert str(world.state_dir) in result.out
        assert (target / "keep").exists()

    def test_the_runtime_state_is_removed_with_the_install(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        (world.state_dir / "shim.log").write_text("ts=1\n")

        world.run("uninstall")

        assert not world.state_dir.exists()

    @pytest.mark.parametrize(
        ("shim_body", "failure_class"),
        [("def broken(:\n", "SyntaxError"), ("raise SystemExit(3)\n", "SystemExit"), ("raise NameError('x')\n", "NameError")],
    )
    def test_uninstall_without_a_loadable_shim_reports_the_load_failure_class_the_owned_count_and_a_foreign_entry_in_full(
        self, fast_world: World, shim_body: str, failure_class: str
    ) -> None:
        world = fast_world
        foreign = {"command": world.adapter.stow_shim_path() + " nonsense"}
        world.hooks_path.write_text(json.dumps({"version": 1, "hooks": {"stop": [foreign]}}))
        world.install()
        owned_count = sum(
            1 for items in hooks_document(world)["hooks"].values() for item in items if world.adapter.ownership(item) == "owned"
        )
        load_adapter_copy_with(world, "hook-shim.py", shim_body)

        result = world.run("uninstall")

        assert result.status == 0, result.err
        assert f"hook-shim.py did not load ({failure_class}), so {owned_count} owned entries were not compared." in result.out
        assert foreign["command"] in result.out
        assert hooks_document(world)["hooks"] == {"stop": [foreign]}

    def test_manifest_candidates_are_removed_only_inside_the_fixed_directories_as_regular_files_with_a_matching_hash(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()
        agents = world.cursor_dir / "agents"
        agents.mkdir()
        (world.cursor_dir / "agents-x").mkdir()
        # The link target's bytes hash to the manifest value, so only the link test can keep the candidate.
        outside = world.tmp_path / "outside.md"
        outside.write_bytes(b"generated")
        files = {}

        def candidate(path: Path, content: bytes = b"generated") -> None:
            if not path.is_symlink():
                path.write_bytes(content)
            files[str(path)] = hashlib.sha256(b"generated").hexdigest()

        removed = agents / "removed.md"
        candidate(removed)
        stale_hash = agents / "stale.md"
        candidate(stale_hash, b"edited since install")
        symlinked = agents / "linked.md"
        symlinked.symlink_to(outside)
        candidate(symlinked)
        sibling = world.cursor_dir / "agents-x" / "sibling.md"
        candidate(sibling)
        dotdot = Path(f"{agents}/../agents-x/dotdot.md")
        (world.cursor_dir / "agents-x" / "dotdot.md").write_bytes(b"generated")
        files[str(dotdot)] = hashlib.sha256(b"generated").hexdigest()
        files[str(agents / "already-gone.md")] = hashlib.sha256(b"generated").hexdigest()
        absent_outside = world.tmp_path / "never-existed.md"
        files[str(absent_outside)] = hashlib.sha256(b"generated").hexdigest()
        (world.record_dir / "manifest.json").write_text(json.dumps({"files": files}))

        result = world.run("uninstall")

        assert not removed.exists()
        assert stale_hash.exists()
        assert symlinked.is_symlink()
        assert outside.read_bytes() == b"generated"
        assert sibling.exists()
        assert (world.cursor_dir / "agents-x" / "dotdot.md").exists()
        for kept in (stale_hash, symlinked, sibling, absent_outside):
            assert str(kept) in result.out
        assert str(agents / "already-gone.md") not in result.out

    def test_a_manifest_candidate_under_a_generated_directory_that_is_a_link_is_kept(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        real_agents = world.tmp_path / "real-agents"
        real_agents.mkdir()
        (world.cursor_dir / "agents").symlink_to(real_agents)
        generated = real_agents / "x.md"
        generated.write_bytes(b"generated")
        candidate = world.cursor_dir / "agents" / "x.md"
        files = {str(candidate): hashlib.sha256(b"generated").hexdigest()}
        (world.record_dir / "manifest.json").write_text(json.dumps({"files": files}))

        result = world.run("uninstall")

        assert generated.read_bytes() == b"generated"
        assert f"Kept {candidate}" in result.out


# --- the install record read by the shim ---


class TestInstallRecordRoundTrip:
    def test_the_record_install_writes_is_read_by_the_real_shim_prologue(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        shim = world.adapter.load_shim()
        call = shim.CallState()

        record = shim.read_install_record(call, require_wrapper=True)

        assert record["routed_config_dir"] == str(world.config_dir)
        assert call.state_dir == str(world.state_dir)

    def test_the_python_the_hooks_path_resolves_is_the_one_install_judged_even_when_a_later_directory_holds_another(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        later = world.tmp_path / "later-bin"
        later.mkdir()
        (world.bin_dir / "jq").rename(later / "jq")
        write_sh_script(later / "python3", "echo '3 13'\n")
        monkeypatch.setenv("PATH", f"{world.bin_dir}:{later}")

        result = world.install()

        assert result.status == 0, result.err
        record = world.install_record()
        assert list(record["tool_paths"])[0] == "python3"
        shim = world.adapter.load_shim()
        resolved = shutil.which("python3", path=shim.hook_environment(record)["PATH"])
        assert resolved == record["tool_paths"]["python3"]


# --- the tamper comparison and status ---


def mutate_remove(document, event, index):
    del document["hooks"][event][index]


def mutate_matcher(document, event, index):
    entry = document["hooks"][event][index]
    entry["matcher"] = "Altered" if "matcher" not in entry else entry["matcher"] + "|X"


def mutate_fail_closed(document, event, index):
    entry = document["hooks"][event][index]
    if "failClosed" in entry:
        del entry["failClosed"]
    else:
        entry["failClosed"] = True


def mutate_timeout(document, event, index):
    document["hooks"][event][index]["timeout"] += 1


def mutate_or_true(document, event, index):
    document["hooks"][event][index]["command"] += " || true"


def mutate_argument(document, event, index):
    entry = document["hooks"][event][index]
    entry["command"] = entry["command"].replace("--deadline 10", "--deadline 11").replace("--hook-env", "--hook-env --x")


def mutate_python(document, event, index):
    entry = document["hooks"][event][index]
    first = entry["command"].split(" ", 1)[0]
    entry["command"] = entry["command"].replace(first, "out=$(/other/python3", 1)


def mutate_tail_document(document, event, index):
    entry = document["hooks"][event][index]
    entry["command"] = entry["command"].replace('"permission":"deny"', '"permission":"allow"')


def mutate_tail_exit(document, event, index):
    document["hooks"][event][index]["command"] = document["hooks"][event][index]["command"].replace("exit 2", "exit 0")


def mutate_move(document, event, index):
    document["hooks"].setdefault("stop", []).append(document["hooks"][event].pop(index))


def mutate_unknown_key(document, event, index):
    document["hooks"][event][index]["unknown"] = 1


def mutate_duplicate(document, event, index):
    document["hooks"][event].append(json.loads(json.dumps(document["hooks"][event][index])))


# case name -> (mutation, applies to fail-closed only, expectation kind)
TAMPER_CASES = {
    "removed": (mutate_remove, False, "missing"),
    "matcher": (mutate_matcher, False, "changed"),
    "fail-closed-flipped": (mutate_fail_closed, False, "changed"),
    "timeout": (mutate_timeout, False, "changed"),
    "or-true-suffix": (mutate_or_true, False, "foreign"),
    "changed-argument": (mutate_argument, False, "argument"),
    "python-path": (mutate_python, False, "changed"),
    "tail-document": (mutate_tail_document, True, "changed"),
    "tail-exit": (mutate_tail_exit, True, "foreign"),
    "moved": (mutate_move, False, "moved"),
    "unknown-key": (mutate_unknown_key, False, "changed"),
    "duplicate": (mutate_duplicate, False, "duplicated"),
}
TAMPER_SHAPES = {
    "matcher-bearing-fail-closed": "require-code-review",
    "matcher-less-fail-closed": "classify",
    "informational": "nudge-worktree-anchor",
    "check-entry": "check",
}
# A tail-rewriting case needs a fail-closed entry: an informational tail already prints `{}` and exits 0, so it has no such case.
SHAPE_CASE_PAIRS = [
    pytest.param(shape, case, id=f"{shape}-{case}")
    for shape in TAMPER_SHAPES
    for case, (_, closed_only, _) in TAMPER_CASES.items()
    if not (closed_only and shape in ("informational", "check-entry"))
]

class TestStatusAndCanary:
    def _emitted(self, world: World):
        return world.adapter.generate_entries(python_path=sys.executable)

    def _live(self, emitted) -> dict:
        document = {"version": 1, "hooks": {}}
        for item in emitted:
            document["hooks"].setdefault(item.event, []).append(json.loads(json.dumps(item.entry)))
        return document

    def test_an_intact_generated_file_compares_clean(self, world: World) -> None:
        emitted = self._emitted(world)
        report = world.adapter.classify_hooks(emitted, self._live(emitted)["hooks"], tolerant=False)
        assert report.clean
        assert report.non_owned == []

    @pytest.mark.parametrize("case", list(TAMPER_CASES))
    def test_the_unit_layer_names_each_case_on_every_generated_entry_it_applies_to(self, world: World, case: str) -> None:
        mutate, closed_only, expectation = TAMPER_CASES[case]
        emitted = self._emitted(world)
        applied = 0
        for target in emitted:
            if closed_only and not target.fail_closed:
                continue
            document = self._live(emitted)
            event = target.event
            index = next(i for i, item in enumerate(document["hooks"][event]) if item == target.entry)
            before = json.dumps(document, sort_keys=True)
            mutate(document, event, index)
            assert json.dumps(document, sort_keys=True) != before, (case, target.label)
            report = world.adapter.classify_hooks(emitted, document["hooks"], tolerant=False)
            applied += 1
            assert not report.clean, (case, target.label)
            expected_kind = expectation
            if expectation == "argument":
                expected_kind = "foreign" if target.label == "check" else "changed"
            if expected_kind == "duplicated":
                assert any(label == target.label for _, _, label in report.duplicated), (case, target.label)
            elif expected_kind == "missing":
                assert (event, target.label) in report.missing, (case, target.label)
            elif expected_kind == "changed":
                assert any(label == target.label for _, _, label in report.changed), (case, target.label)
            elif expected_kind == "foreign":
                assert len(report.foreign) == 1 and (event, target.label) in report.missing, (case, target.label)
            elif expected_kind == "moved":
                assert (event, target.label) in report.missing, (case, target.label)
                assert any(label == target.label for _, _, label in report.extra_owned), (case, target.label)
        assert applied

    @pytest.mark.parametrize("surface", [surface for surface in SURFACES if surface != HOOK_ENV_SURFACE])
    @pytest.mark.parametrize(("shape", "case"), SHAPE_CASE_PAIRS)
    def test_status_and_check_name_each_case_on_one_entry_of_each_shape(
        self, fast_world: World, surface: str, shape: str, case: str
    ) -> None:
        world = fast_world
        mutate, _, expectation = TAMPER_CASES[case]
        label = TAMPER_SHAPES[shape]
        world.install()
        document = hooks_document(world)
        event, index = find_entry(world, document, label)
        mutate(document, event, index)
        save_hooks(world, document)

        result = run_surface(world, surface)

        assert result.status == (1 if surface == "check" else 0), (surface, shape, case)
        for line in expected_tamper_lines(case, expectation, label, event, index):
            assert f"finding: {line}" in result.out, (surface, shape, case, line)

    @pytest.mark.parametrize("case", [case for case in TAMPER_CASES])
    def test_the_hook_env_check_names_each_case_on_another_entry(self, fast_world: World, case: str) -> None:
        world = fast_world
        mutate, _, expectation = TAMPER_CASES[case]
        label = "require-code-review"
        world.install()
        document = hooks_document(world)
        event, index = find_entry(world, document, label)
        mutate(document, event, index)
        save_hooks(world, document)

        result = run_surface(world, HOOK_ENV_SURFACE)

        assert result.status == 0
        for line in expected_tamper_lines(case, expectation, label, event, index):
            assert f"finding: {line}" in result.out.splitlines(), (case, line)

    @pytest.mark.parametrize("surface", SURFACES)
    def test_an_intact_install_reports_no_tamper_finding_on_any_surface(self, fast_world: World, surface: str) -> None:
        world = fast_world
        world.install()

        result = run_surface(world, surface)

        assert result.status == 0, result.out
        for phrase in TAMPER_PHRASES:
            assert phrase not in result.out, (surface, phrase)
        assert "finding:" not in result.out

    @pytest.mark.parametrize("surface", SURFACES)
    def test_a_foreign_hostile_entry_and_a_hostile_matcher_are_absent_from_every_report_and_the_log(
        self, fast_world: World, surface: str
    ) -> None:
        world = fast_world
        nonce = "NONCE-" + "7f3a"
        world.install()
        document = hooks_document(world)
        document["hooks"].setdefault("stop", []).append({"command": f"{nonce} {world.adapter.stow_shim_path()} --x"})
        foreign_line = f"foreign entry that holds an adapter path: stop [{len(document['hooks']['stop']) - 1}]"
        event, index = find_entry(world, document, "require-code-review")
        document["hooks"][event][index]["matcher"] = f"{nonce}-matcher"
        save_hooks(world, document)
        shown_by_install_data = world.adapter.print_set(
            world.adapter.classify_hooks(
                world.adapter.generate_entries(python_path=sys.executable), document["hooks"], tolerant=True
            ),
            document["hooks"],
            {},
            replacing_pre_image=False,
            comparable=True,
        )

        report = run_surface(world, surface).out
        log = (world.state_dir / "shim.log").read_text() if (world.state_dir / "shim.log").exists() else ""

        assert any(nonce in line for line in shown_by_install_data)
        assert nonce not in report
        assert nonce not in log
        assert foreign_line in report

    def test_an_event_key_outside_the_vocabulary_is_counted_as_other_in_the_reports(self, world: World) -> None:
        assert world.adapter.safe_event_name("stop") == "stop"
        assert world.adapter.safe_event_name("\x1b[31mevil") == "other"
        assert world.adapter.safe_label("require-code-review", {"require-code-review"}) == "require-code-review"
        assert world.adapter.safe_label("\x1b[31m", {"require-code-review"}) == "other"

    @pytest.mark.parametrize("surface", SURFACES)
    def test_a_hostile_event_key_reaches_no_report_and_is_counted_as_other(self, fast_world: World, surface: str) -> None:
        world = fast_world
        world.install()
        document = hooks_document(world)
        hostile_key = HOSTILE_EVENT_KEY
        event, index = find_entry(world, document, "require-code-review")
        document["hooks"][hostile_key] = [{"command": "echo planted"}, dict(document["hooks"][event][index])]
        save_hooks(world, document)

        report = run_surface(world, surface).out

        assert "non-owned entries by event: other 1" in report
        assert "owned entry with no emitted counterpart: require-code-review on other" in report
        for fragment in HOSTILE_EVENT_KEY_FRAGMENTS:
            assert fragment not in report, (surface, fragment)

    @pytest.mark.parametrize("surface", SURFACES)
    def test_a_foreign_entry_under_a_hostile_event_key_is_named_by_other_and_position_without_the_key(
        self, fast_world: World, surface: str
    ) -> None:
        world = fast_world
        world.install()
        document = hooks_document(world)
        document["hooks"][HOSTILE_EVENT_KEY] = [{"command": f"{world.adapter.stow_shim_path()} --x"}]
        save_hooks(world, document)

        report = run_surface(world, surface).out

        assert "foreign entry that holds an adapter path: other [0]" in report
        for fragment in HOSTILE_EVENT_KEY_FRAGMENTS:
            assert fragment not in report, (surface, fragment)

    @pytest.mark.parametrize("surface", SURFACES)
    def test_an_unsaved_non_owned_entry_under_a_hostile_event_key_is_named_by_other_and_position_without_the_key(
        self, fast_world: World, surface: str
    ) -> None:
        world = fast_world
        world.hooks_path.write_bytes(ORIGINAL_HOOKS)
        world.install()
        document = hooks_document(world)
        document["hooks"] = {HOSTILE_EVENT_KEY: [{"command": "echo planted"}]}
        save_hooks(world, document)

        report = run_surface(world, surface).out

        assert "non-owned entry the pre-install file does not hold: other [0]" in report
        for fragment in HOSTILE_EVENT_KEY_FRAGMENTS:
            assert fragment not in report, (surface, fragment)

    @pytest.mark.parametrize("command", ["install", "uninstall"])
    def test_install_and_uninstall_print_a_hostile_event_key_escaped_and_cut_with_its_full_length(
        self, fast_world: World, command: str
    ) -> None:
        world = fast_world
        world.install()
        document = hooks_document(world)
        hostile_key = HOSTILE_EVENT_KEY
        event, index = find_entry(world, document, "require-code-review")
        document["hooks"][hostile_key] = [dict(document["hooks"][event][index])]
        save_hooks(world, document)

        result = world.install() if command == "install" else world.run("uninstall")

        assert result.status == 0, result.err
        for fragment in ("\x1b", "\x85", "‮", "\nHOSTILEKEY"):
            assert fragment not in result.out
        assert "\\x1b" in result.out
        assert "\\u202e" in result.out
        assert "characters in all]" in result.out
        assert max(len(line) for line in result.out.splitlines()) < 2000

    def _write_log(self, world: World, fields_list: list[dict]) -> None:
        shim = world.adapter.load_shim()
        world.state_dir.mkdir(exist_ok=True)
        (world.state_dir / "shim.log").write_bytes(b"".join(shim.format_log_line(fields) for fields in fields_list))

    def test_status_counts_pin_check_denies_by_cause_and_a_quiet_path_counts_nothing(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        self._write_log(
            world,
            [
                {"reg": "pin-check", "out": "ok"},
                {"reg": "pin-check", "out": "pin-check"},
                {"reg": "pin-check", "out": "pin-check"},
            ],
        )
        assert "pin-check denies: 2" in world.run("status").out
        self._write_log(world, [{"reg": "pin-check", "out": "pin-check-ok"}])
        assert "pin-check denies" not in world.run("status").out

    def test_status_counts_stale_install_calls_and_shows_a_shim_fault_class_beside_its_python(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        self._write_log(
            world,
            [
                {"reg": "x", "out": "stale-install"},
                {"reg": "x", "out": "shim-fault", "fault": "RuntimeError", "py": "3.14"},
            ],
        )

        out = world.run("status").out

        assert "stale-install calls: 1" in out
        assert "shim fault: class RuntimeError on python 3.14" in out

    def test_status_names_the_overflow_token_as_an_anomaly_not_a_registration(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        self._write_log(world, [{"reg": "x" * 200, "out": "ok"}])
        out = world.run("status").out
        assert "anomaly:" in out
        assert "registration overflow" not in out

    @pytest.mark.parametrize(
        ("lines", "silent"),
        [
            (
                [{"reg": "check-hook-env"}, {"reg": "a", "py": "3.14"}, {"reg": "check-hook-env"}, {"reg": "b", "py": "3.14"}],
                False,
            ),
            ([{"reg": "check-hook-env"}, {"reg": "a", "py": "3.14"}, {"reg": "b", "py": "3.14"}], True),
            ([{"reg": "a", "py": "3.14"}, {"reg": "check-hook-env"}, {"reg": "b", "py": "3.14"}], False),
            ([{"reg": "a", "py": "3.14"}, {"reg": "b", "py": "3.14"}], True),
        ],
        ids=["interleaved-quiet", "two-first-lines-after-the-last-check", "one-first-line-after-a-check", "two-with-no-check"],
    )
    def test_status_flags_a_silent_session_start_entry_only_after_two_shim_first_lines_follow_its_last_check(
        self, fast_world: World, lines: list[dict], silent: bool
    ) -> None:
        world = fast_world
        world.install()
        self._write_log(world, lines)
        assert ("looks silent" in world.run("status").out) is silent

    def test_status_flags_a_log_past_the_rotation_threshold(self, fast_world: World, monkeypatch: pytest.MonkeyPatch) -> None:
        world = fast_world
        world.install()
        self._write_log(world, [{"reg": "a", "out": "ok"}])
        monkeypatch.setattr(world.adapter, "LOG_ROTATE_BYTES", 1)
        assert "past the rotation threshold" in world.run("status").out

    def test_a_hook_env_check_with_no_payload_makes_exactly_one_log_line(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        world.run("check", "--hook-env")
        assert [f["reg"] for f in world.adapter.read_log(str(world.state_dir)).lines] == ["check-hook-env"]


# --- plain check ---


class TestCheckCommand:
    def test_check_on_an_intact_install_prints_the_python_and_exits_zero(self, world: World) -> None:
        assert world.install().status == 0
        result = world.run("check")
        assert result.status == 0, result.out + result.err
        assert f"recorded python3: {world.bin_dir / 'python3'}" in result.out

    def test_check_reports_a_settings_link_that_does_not_resolve_to_the_tracked_file(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        (world.config_dir / "settings.json").unlink()
        (world.config_dir / "settings.json").write_text("{}")

        result = world.run("check")

        assert result.status == 1
        assert "settings.json does not resolve" in result.out

    def test_check_reports_a_tool_map_that_differs_from_the_recorded_copy(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        record = world.install_record()
        record["tool_map"]["tools"]["Extra"] = {"claude": "Extra", "translation": "passthrough"}
        (world.record_dir / "install.json").write_text(json.dumps(record))

        result = world.run("check")

        assert "tool-map.json differs" in result.out

    def test_check_with_no_install_says_so(self, world: World) -> None:
        result = world.run("check")
        assert result.status == 1
        assert "No usable install record" in result.out

    def test_a_removed_wrapper_gives_the_wrapper_path_report_and_the_self_check_still_runs_without_stopping_at_the_first_failure(
        self, world: World
    ) -> None:
        assert world.install().status == 0
        wrapper = world.bin_dir / wrapper_name()
        wrapper.unlink()

        checked = world.run("check")
        context = json.loads(world.run("check", "--hook-env").out)["additional_context"]

        assert checked.status == 1
        assert f"{wrapper} could not be started (cause ENOENT)" in checked.out
        assert f"The wrapper {wrapper} could not be started at that path (cause ENOENT)" in checked.out
        assert "hook-run" not in checked.out
        assert "GNU coreutils' `timeout`" not in checked.out
        assert f"{wrapper} could not be started (cause ENOENT)" in context

    def test_a_wrapper_in_place_reports_no_wrapper_fault(self, world: World) -> None:
        assert world.install().status == 0
        context = json.loads(world.run("check", "--hook-env").out)["additional_context"]
        assert "could not be started" not in context
        assert "GNU coreutils" not in context

    def test_a_recorded_python3_that_cannot_start_is_reported_as_not_started_and_not_as_printing_no_version(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()
        missing = str(world.tmp_path / "removed" / "python3")
        record = world.install_record()
        record["tool_paths"]["python3"] = missing
        (world.record_dir / "install.json").write_text(json.dumps(record))

        result = world.run("check")

        assert f"recorded python3: {missing} (could not be started, cause ENOENT)" in result.out
        assert f"The recorded python3 at {missing} could not be started (cause ENOENT)" in result.out
        assert "printed no version" not in result.out

    def test_the_hook_env_report_points_a_claudecode_name_at_its_recovery_entry_and_says_nothing_without_one(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        world.install()
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
        assert "Recovery section's CLAUDECODE entry" not in run_surface(world, HOOK_ENV_SURFACE).out
        monkeypatch.setenv("CLAUDECODE", "1")

        context = run_surface(world, HOOK_ENV_SURFACE).out

        assert "Claude Code environment names set in this hook: CLAUDECODE" in context
        assert "Recovery section's CLAUDECODE entry" in context

    def test_a_clean_check_says_so(self, fast_world: World) -> None:
        world = fast_world
        world.install()

        result = world.run("check")

        assert result.status == 0
        assert "no findings" in result.out

    def test_check_reports_a_hook_script_a_stale_install_no_longer_finds(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        missing = world.adapter.stow_hook_path("require-code-review.sh")
        stowed_hooks = world.home / ".claude" / "hooks"
        stowed_hooks.unlink()
        stowed_hooks.mkdir()
        for hook_file in (CLAUDE_DIR / "hooks").iterdir():
            if str(stowed_hooks / hook_file.name) != missing:
                (stowed_hooks / hook_file.name).symlink_to(hook_file)

        result = world.run("check")

        assert result.status == 1
        assert f"finding: {missing} is missing, so an entry in hooks.json cannot run: a stale install." in result.out


# --- hooks.json members a report must show ---


class TestPlantedEntriesAndTopLevelMembers:
    @pytest.mark.parametrize("surface", SURFACES)
    def test_a_planted_entry_beside_an_intact_adapter_set_is_counted_by_event_and_never_printed(
        self, fast_world: World, surface: str
    ) -> None:
        world = fast_world
        world.install()
        document = hooks_document(world)
        document["hooks"]["preToolUse"].append({"command": "/tmp/PLANTEDNONCE-one.sh"})
        document["hooks"]["beforeShellExecution"] += [
            {"command": "/tmp/PLANTEDNONCE-two.sh"},
            {"command": "/tmp/PLANTEDNONCE-three.sh"},
        ]
        save_hooks(world, document)

        result = run_surface(world, surface)

        assert "non-owned entries by event: beforeShellExecution 2, preToolUse 1" in result.out
        assert "PLANTEDNONCE" not in result.out
        assert result.status == 0

    @pytest.mark.parametrize("surface", SURFACES)
    def test_a_planted_entry_that_holds_an_adapter_path_is_a_foreign_finding_on_every_surface(
        self, fast_world: World, surface: str
    ) -> None:
        world = fast_world
        world.install()
        document = hooks_document(world)
        document["hooks"]["preToolUse"].append({"command": f"/tmp/PLANTEDNONCE.sh {world.adapter.stow_shim_path()}"})
        position = len(document["hooks"]["preToolUse"]) - 1
        save_hooks(world, document)

        result = run_surface(world, surface)

        assert f"foreign entry that holds an adapter path: preToolUse [{position}]" in result.out
        assert world.adapter.FOREIGN_FIX_SENTENCE in result.out
        assert world.adapter.TAMPER_FIX_SENTENCE not in result.out
        assert "PLANTEDNONCE" not in result.out
        assert result.status == (1 if surface == "check" else 0)

    @pytest.mark.parametrize("surface", SURFACES)
    def test_foreign_entries_are_named_by_event_and_position_up_to_the_print_cap_and_counted_beyond_it(
        self, fast_world: World, surface: str
    ) -> None:
        world = fast_world
        world.install()
        document = hooks_document(world)
        extra = world.adapter.PRINT_SET_MAX_ENTRIES + 3
        document["hooks"]["stop"] = [{"command": f"{world.adapter.stow_shim_path()} {number}"} for number in range(extra)]
        save_hooks(world, document)

        out = run_surface(world, surface).out

        assert out.count("foreign entry that holds an adapter path: stop [") == world.adapter.PRINT_SET_MAX_ENTRIES
        assert "3 more foreign entry(s) not shown" in out

    @pytest.mark.parametrize("surface", SURFACES)
    @pytest.mark.parametrize("version", [2, 0, "1", True, 1.0, None], ids=repr)
    def test_a_top_level_version_other_than_the_one_cursor_reads_is_a_finding_on_every_surface(
        self, fast_world: World, surface: str, version: object
    ) -> None:
        world = fast_world
        world.install()
        document = hooks_document(world)
        document["version"] = version
        save_hooks(world, document)

        result = run_surface(world, surface)

        assert f"the top-level version of hooks.json is not {world.adapter.HOOKS_JSON_VERSION}" in result.out
        assert world.adapter.VERSION_FIX_SENTENCE in result.out
        assert result.status == (1 if surface == "check" else 0)

    @pytest.mark.parametrize("surface", SURFACES)
    def test_a_removed_top_level_version_is_a_finding_on_every_surface(self, fast_world: World, surface: str) -> None:
        world = fast_world
        world.install()
        document = hooks_document(world)
        del document["version"]
        save_hooks(world, document)

        result = run_surface(world, surface)

        assert "the top-level version of hooks.json is not" in result.out
        assert world.adapter.VERSION_FIX_SENTENCE in result.out

    @pytest.mark.parametrize("surface", SURFACES)
    def test_top_level_members_changed_or_added_after_the_install_are_counted_and_never_named(
        self, fast_world: World, surface: str
    ) -> None:
        world = fast_world
        world.hooks_path.write_bytes(ORIGINAL_HOOKS)
        world.install()
        document = hooks_document(world)
        document["extra"] = [3]
        document["memberNONCE"] = "valueNONCE"
        save_hooks(world, document)

        result = run_surface(world, surface)

        assert "top-level members of hooks.json that differ from the pre-install file: 2" in result.out
        assert "NONCE" not in result.out

    @pytest.mark.parametrize("surface", SURFACES)
    def test_top_level_members_the_install_kept_unchanged_are_no_finding(self, fast_world: World, surface: str) -> None:
        world = fast_world
        world.hooks_path.write_bytes(ORIGINAL_HOOKS)
        world.install()

        assert "top-level" not in run_surface(world, surface).out

    @pytest.mark.parametrize("surface", SURFACES)
    def test_with_no_pre_install_file_any_member_beyond_version_and_hooks_is_counted(
        self, fast_world: World, surface: str
    ) -> None:
        world = fast_world
        world.install()
        document = hooks_document(world)
        document["added"] = 1
        save_hooks(world, document)

        assert "top-level members of hooks.json that differ from the pre-install file: 1" in run_surface(world, surface).out

    @pytest.mark.parametrize("surface", SURFACES)
    def test_with_every_owned_entry_removed_each_non_owned_entry_the_pre_install_file_lacks_is_named_by_event_and_position(
        self, fast_world: World, surface: str
    ) -> None:
        world = fast_world
        world.hooks_path.write_bytes(ORIGINAL_HOOKS)
        world.install()
        document = hooks_document(world)
        document["hooks"] = {"stop": [{"command": "echo mine"}, {"command": "/tmp/PLANTEDNONCE"}]}
        save_hooks(world, document)

        result = run_surface(world, surface)

        assert "non-owned entry the pre-install file does not hold: stop [1]" in result.out
        assert "stop [0]" not in result.out
        assert "PLANTEDNONCE" not in result.out
        assert result.status == (1 if surface == "check" else 0)

    @pytest.mark.parametrize("surface", SURFACES)
    def test_with_an_owned_entry_remaining_the_pre_install_files_own_entries_raise_no_by_position_finding(
        self, fast_world: World, surface: str
    ) -> None:
        world = fast_world
        world.hooks_path.write_bytes(ORIGINAL_HOOKS)
        world.install()

        result = run_surface(world, surface)

        assert "non-owned entry the pre-install file does not hold" not in result.out
        assert "non-owned entries by event: stop 1" in result.out


# --- the sessionStart check's writes ---


def directory_tree(root: Path) -> dict[str, tuple[bool, bytes | None, int]]:
    """Every entry under `root`: whether it is a link, its bytes when a regular file, and its modification time."""
    return {
        str(path): (
            path.is_symlink(),
            path.read_bytes() if path.is_file() and not path.is_symlink() else None,
            path.lstat().st_mtime_ns,
        )
        for path in sorted(root.rglob("*"))
    }


def run_hook_env_process(world: World, payload: dict | None) -> dict:
    """The real sessionStart command under `-I -S` with the payload on stdin, as the launch guard runs it."""
    completed = subprocess.run(
        [sys.executable, "-I", "-S", str(ADAPTER_SOURCE), "check", "--hook-env"],
        input=json.dumps(payload).encode() if payload is not None else b"",
        capture_output=True,
        env={"HOME": str(world.home), "PATH": str(world.bin_dir)},
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


SESSION_START_PAYLOAD = {"hook_event_name": "sessionStart", "conversation_id": CONVERSATION_ID}


class TestHookEnvStateDirectory:
    def _link_state_dir_to(self, world: World, target: Path) -> None:
        shutil.rmtree(world.state_dir)
        world.state_dir.symlink_to(target)

    def test_a_state_dir_that_is_a_link_gets_no_log_line_mark_sweep_or_rotation_through_it(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        world.install()
        target = world.tmp_path / "elsewhere"
        (target / "first-call").mkdir(parents=True)
        old_mark = target / "first-call" / CONVERSATION_ID
        old_mark.write_text("")
        os.utime(old_mark, (1, 1))
        (target / "shim.log").write_text("existing line\n")
        monkeypatch.setattr(world.adapter, "LOG_ROTATE_BYTES", 1)
        self._link_state_dir_to(world, target)
        before = directory_tree(target)

        result = run_surface(world, HOOK_ENV_SURFACE)

        assert directory_tree(target) == before
        assert not (target / "rotate.lock").exists()
        assert "is missing or not a real directory" in result.out

    def test_a_first_call_dir_that_is_a_link_is_neither_swept_nor_given_a_mark_and_the_log_line_still_lands(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()
        target = world.tmp_path / "elsewhere"
        target.mkdir()
        old_mark = target / CONVERSATION_ID
        old_mark.write_text("")
        os.utime(old_mark, (1, 1))
        (world.state_dir / "first-call").symlink_to(target)
        before = directory_tree(target)

        result = run_surface(world, HOOK_ENV_SURFACE)

        assert directory_tree(target) == before
        assert "first-call directory" in result.out
        assert [fields["reg"] for fields in world.adapter.read_log(str(world.state_dir)).lines] == ["check-hook-env"]

    def test_real_directories_get_the_log_line_the_rotation_and_the_sweep(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        world.install()
        (world.state_dir / "first-call").mkdir()
        old_mark = world.state_dir / "first-call" / CONVERSATION_ID
        old_mark.write_text("")
        os.utime(old_mark, (1, 1))
        (world.state_dir / "shim.log").write_text("existing line\n")
        monkeypatch.setattr(world.adapter, "LOG_ROTATE_BYTES", 1)

        result = run_surface(world, HOOK_ENV_SURFACE)

        assert "is missing or not a real directory" not in result.out
        assert (world.state_dir / "shim.log.1").read_text() == "existing line\n"
        assert not old_mark.exists()
        assert [fields["reg"] for fields in world.adapter.read_log(str(world.state_dir)).lines] == ["check-hook-env"]

    def test_status_with_a_linked_state_dir_prints_one_line_for_it_and_reads_no_log(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        target = world.tmp_path / "elsewhere"
        target.mkdir()
        (target / "shim.log").write_bytes(world.adapter.load_shim().format_log_line({"reg": "planted", "out": "ok"}))
        self._link_state_dir_to(world, target)

        result = world.run("status")

        assert result.out.count("is missing or not a real directory") == 1
        assert "planted" not in result.out
        assert "registration " not in result.out


class TestHookEnvAlwaysAnswers:
    def _assert_one_line_document_naming(self, result: Result, *fragments: str) -> None:
        assert result.status == 0
        assert len(result.out.splitlines()) == 1
        context = json.loads(result.out)["additional_context"]
        for fragment in fragments:
            assert fragment in context

    def test_a_comparison_that_raises_an_unexpected_error_still_gives_status_zero_and_one_document_naming_the_class(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        world.install()

        def raising_comparison(record, hooks_path, generation_failure_fix):
            raise RuntimeError("boom")

        monkeypatch.setattr(world.adapter, "compare_install", raising_comparison)

        result = world.run("check", "--hook-env")

        self._assert_one_line_document_naming(result, "check failed (RuntimeError)")

    def test_a_tool_re_test_that_raises_an_unexpected_error_still_answers_and_the_log_line_is_written(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        world.install()

        def raising_re_test(record, command_name):
            raise RuntimeError("boom")

        monkeypatch.setattr(world.adapter, "tool_findings", raising_re_test)

        result = world.run("check", "--hook-env")

        self._assert_one_line_document_naming(result, "The tool re-test failed (RuntimeError)")
        assert [fields["reg"] for fields in world.adapter.read_log(str(world.state_dir)).lines] == ["check-hook-env"]


class EntryKilled(BaseException):
    """A kill of the sessionStart entry at a chosen point. It is no Exception, because a killed process runs no handler."""


class TestFirstCallMark:
    """The sessionStart entry and the shim's first call race for one mark per conversation. These run the entry as a real
    process with a payload on stdin, and call the shim's claim in each order."""

    def _marks(self, world: World) -> list[str]:
        directory = world.state_dir / "first-call"
        return sorted(os.listdir(directory)) if directory.is_dir() else []

    def _log_regs(self, world: World) -> list[str]:
        return [fields["reg"] for fields in world.adapter.read_log(str(world.state_dir)).lines]

    def test_the_entry_first_makes_the_mark_after_its_one_log_line_and_the_shims_claim_then_loses(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()

        run_hook_env_process(world, SESSION_START_PAYLOAD)

        assert self._marks(world) == [CONVERSATION_ID]
        assert self._log_regs(world) == ["check-hook-env"]
        assert load_shim().claim_first_call(str(world.state_dir), CONVERSATION_ID) is False

    def test_the_shim_first_keeps_its_mark_and_the_entry_neither_replaces_it_nor_makes_a_second(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()
        assert load_shim().claim_first_call(str(world.state_dir), CONVERSATION_ID) is True
        mark = world.state_dir / "first-call" / CONVERSATION_ID
        inode = mark.stat().st_ino

        run_hook_env_process(world, SESSION_START_PAYLOAD)

        assert self._marks(world) == [CONVERSATION_ID]
        assert mark.stat().st_ino == inode
        assert self._log_regs(world) == ["check-hook-env"]

    def test_an_entry_killed_after_its_log_line_and_before_its_mark_leaves_the_log_line_no_mark_and_the_shims_claim_wins(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        world.install()
        shim = world.adapter.load_shim()
        real_claim_first_call = shim.claim_first_call

        def killed_before_the_mark(state_dir, session_id):
            raise EntryKilled

        monkeypatch.setattr(world.adapter, "hook_env_conversation_id", lambda shim_module: CONVERSATION_ID)
        monkeypatch.setattr(shim, "claim_first_call", killed_before_the_mark)

        with contextlib.redirect_stdout(io.StringIO()), pytest.raises(EntryKilled):
            world.adapter.hook_env_check_body()

        assert self._log_regs(world) == ["check-hook-env"]
        assert self._marks(world) == []
        assert real_claim_first_call(str(world.state_dir), CONVERSATION_ID) is True

    def test_a_log_line_that_failed_to_append_leaves_no_mark(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        (world.state_dir / "shim.log").mkdir()

        document = run_hook_env_process(world, SESSION_START_PAYLOAD)

        assert "additional_context" in document
        assert self._marks(world) == []

    def test_a_run_with_no_payload_makes_a_log_line_and_no_mark(self, fast_world: World) -> None:
        world = fast_world
        world.install()

        run_hook_env_process(world, None)

        assert self._log_regs(world) == ["check-hook-env"]
        assert self._marks(world) == []


# --- what `status` reads from the log ---


def write_raw_log(world: World, data: bytes) -> None:
    world.state_dir.mkdir(exist_ok=True)
    (world.state_dir / "shim.log").write_bytes(data)


class TestStatusLogReading:
    def test_a_value_outside_the_log_grammar_or_the_known_labels_is_counted_as_other_and_never_printed(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()
        write_raw_log(
            world,
            b"ts=1 reg=\x1b]52;c;QUJD out=ok\nts=2 reg=planted-label out=ok\n"
            b"ts=3 reg=check-hook-env out=shim-fault fault=\x1b[31m py=\x9b\n",
        )

        out = world.run("status").out

        assert "\x1b" not in out
        assert "planted-label" not in out
        assert "registration other: 2 call(s)" in out
        assert "shim fault: class other on python other: 1 call(s)" in out

    def test_a_log_of_a_hundred_thousand_distinct_labels_is_read_under_the_caps_and_prints_one_other_line(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()
        limit = world.adapter.LOG_STATUS_MAX_LINES
        write_raw_log(world, b"".join(f"reg=label{index} out=ok\n".encode() for index in range(2 * limit)))

        out = world.run("status").out

        assert f"registration other: {limit} call(s)" in out
        assert "the log is larger than `status` reads" in out
        assert len(out.splitlines()) < 40

    def test_a_log_file_past_the_byte_cap_is_read_from_its_end(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        padding = b"reg=padding out=ok\n" * (world.adapter.LOG_STATUS_READ_BYTES // 19 + 10)
        write_raw_log(world, padding + b"reg=check-hook-env out=ok\n")

        lines, cut = world.adapter.read_log_tail(str(world.state_dir / "shim.log"))

        assert cut is True
        assert lines[-1] == b"reg=check-hook-env out=ok"
        assert sum(len(line) + 1 for line in lines) <= world.adapter.LOG_STATUS_READ_BYTES

    def test_faults_are_aggregated_by_class_and_python_with_a_count_and_capped_with_a_count_of_the_rest(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()
        shim = world.adapter.load_shim()
        kinds = world.adapter.PRINT_SET_MAX_ENTRIES + 3
        lines = [{"reg": "x", "out": "shim-fault", "fault": f"Fault{index}", "py": "3.14"} for index in range(kinds)]
        lines += [{"reg": "x", "out": "shim-fault", "fault": "Fault0", "py": "3.14"}] * 4
        write_raw_log(world, b"".join(shim.format_log_line(fields) for fields in lines))

        out = world.run("status").out

        assert "shim fault: class Fault0 on python 3.14: 5 call(s)" in out
        assert out.count("shim fault: class ") == world.adapter.PRINT_SET_MAX_ENTRIES
        assert "3 more shim fault kind(s) not shown" in out


# --- a record or a comparison that cannot be used ---


class TestDamagedRecord:
    @pytest.mark.parametrize("surface", SURFACES)
    @pytest.mark.parametrize("tool_kind", ["python3", "wrapper", "jq"])
    @pytest.mark.parametrize("kind", ["relative", "dot-dot", "colon", "newline", "nul"])
    def test_a_record_path_the_shims_rule_refuses_is_refused_by_every_report_and_its_file_never_runs(
        self, fast_world: World, surface: str, tool_kind: str, kind: str
    ) -> None:
        world = fast_world
        world.install()
        tool_name = wrapper_name() if tool_kind == "wrapper" else tool_kind
        base = world.tmp_path / "planted"
        (base / "x").mkdir(parents=True)
        (world.cursor_dir / "reldir").mkdir()
        recorded = {
            "relative": f"reldir/{tool_name}",
            "dot-dot": f"{base}/x/../{tool_name}",
            "colon": f"{base}/x/a:b/{tool_name}",
            "newline": f"{base}/x/a\nb/{tool_name}",
            "nul": f"{base}/x/a\0b/{tool_name}",
        }[kind]
        canary = world.tmp_path / "recorded-file-ran"
        if kind != "nul":  # a path with a NUL names no file, so there is nothing to plant
            planted = world.cursor_dir / recorded if kind == "relative" else Path(recorded)
            planted.parent.mkdir(parents=True, exist_ok=True)
            write_sh_script(planted, f": > {canary}\n")
        record = world.install_record()
        record["tool_paths"][tool_name] = recorded
        (world.record_dir / "install.json").write_text(json.dumps(record))

        result = run_surface(world, surface)

        assert not canary.exists()
        self._assert_record_refused(result, surface)

    @pytest.mark.parametrize("surface", SURFACES)
    @pytest.mark.parametrize("kind", ["relative", "dot-dot", "colon", "newline", "nul"])
    def test_a_routed_config_dir_the_shims_rule_refuses_is_refused_by_every_report(
        self, fast_world: World, surface: str, kind: str
    ) -> None:
        world = fast_world
        world.install()
        record = world.install_record()
        record["routed_config_dir"] = {
            "relative": "config/dir",
            "dot-dot": f"{world.config_dir}/../config",
            "colon": f"{world.config_dir}/a:b",
            "newline": f"{world.config_dir}/a\nb",
            "nul": f"{world.config_dir}/a\0b",
        }[kind]
        (world.record_dir / "install.json").write_text(json.dumps(record))

        result = run_surface(world, surface)

        self._assert_record_refused(result, surface)

    @staticmethod
    def _assert_record_refused(result: Result, surface: str) -> None:
        if surface == HOOK_ENV_SURFACE:
            assert "install record is unreadable" in result.out
        else:
            assert result.status == 1
            assert "No usable install record" in result.out
            assert "holds a path that is not absolute" in result.out

    @pytest.mark.parametrize("surface", ["status", "check"])
    @pytest.mark.parametrize("python3", [None, 5], ids=["absent", "not-a-string"])
    def test_a_record_with_no_usable_python3_is_named_not_raised(self, fast_world: World, surface: str, python3: object) -> None:
        world = fast_world
        world.install()
        record = world.install_record()
        if python3 is None:
            del record["tool_paths"]["python3"]
        else:
            record["tool_paths"]["python3"] = python3
        (world.record_dir / "install.json").write_text(json.dumps(record))

        result = world.run(surface)

        assert result.status == 1
        assert "names no python3" in result.out

    @pytest.mark.parametrize("surface", SURFACES)
    def test_a_comparison_that_cannot_be_built_is_a_finding_and_every_other_finding_still_prints(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch, surface: str
    ) -> None:
        world = fast_world
        world.install()
        (world.bin_dir / wrapper_name()).unlink()

        def unbuildable(**keywords):
            raise world.adapter.AdapterError("a registration with no counterpart")

        monkeypatch.setattr(world.adapter, "generate_entries", unbuildable)

        result = run_surface(world, surface)

        assert "The comparison failed (AdapterError: a registration with no counterpart)." in result.out
        if surface != "status":
            assert "could not be started (cause ENOENT)" in result.out
        assert result.status == (1 if surface == "check" else 0)

    @pytest.mark.parametrize("text", ["NaN", "Infinity", "-Infinity"])
    def test_an_install_record_the_shim_would_refuse_is_unusable_to_the_adapter_too(self, fast_world: World, text: str) -> None:
        world = fast_world
        world.install()
        record = (world.record_dir / "install.json").read_text()
        (world.record_dir / "install.json").write_text(record.rstrip().removesuffix("}") + f', "extra": {text}' + "}")

        result = world.run("check")

        assert result.status == 1
        assert "No usable install record" in result.out

    def test_a_hooks_file_with_a_non_finite_number_is_refused_by_install_with_nothing_written(self, fast_world: World) -> None:
        world = fast_world
        world.hooks_path.write_text('{"version": 1, "hooks": {}, "limit": NaN}')
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert str(world.hooks_path) in result.err
        assert world.snapshot() == before

    def test_the_bytes_the_adapter_writes_never_hold_a_non_finite_number(self, world: World) -> None:
        with pytest.raises(ValueError):
            world.adapter.json_bytes({"limit": float("nan")})


# --- a failure after the first write ---


# An exception for each class the write blocks must treat alike, with the cause the message names.
WRITE_FAILURES = {
    "oserror": (lambda adapter, path: OSError(errno.EACCES, "Permission denied", path), "EACCES"),
    "runtime-error": (lambda adapter, path: RuntimeError("boom"), "RuntimeError"),
    "adapter-error": (lambda adapter, path: adapter.AdapterError("boom"), "AdapterError"),
}


class TestFailureAfterTheFirstWrite:
    def test_an_oserror_after_the_first_install_write_has_its_own_exit_status_names_the_step_and_a_later_uninstall_restores(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        world.hooks_path.write_bytes(ORIGINAL_HOOKS)
        real_write = world.adapter.durable_write

        def failing_write(path, data, mode=0o600):
            if os.path.basename(path) == "manifest.json":
                raise OSError(errno.ENOSPC, "No space left on device", path)
            return real_write(path, data, mode)

        monkeypatch.setattr(world.adapter, "durable_write", failing_write)

        result = world.install()

        assert result.status == world.adapter.PARTIAL_WRITE_EXIT_STATUS
        assert result.status != 1
        assert "failed after writes began" in result.err
        assert "after-hooks-json" in result.err
        assert "ENOSPC" in result.err
        assert "`uninstall`" in result.err
        assert "Traceback" not in result.err
        monkeypatch.setattr(world.adapter, "durable_write", real_write)
        assert world.run("uninstall").status == 0
        assert world.hooks_path.read_bytes() == ORIGINAL_HOOKS

    def test_a_refusal_before_any_write_keeps_exit_status_one(self, fast_world: World) -> None:
        result = fast_world.run("install", "--config-dir", str(fast_world.config_dir))

        assert result.status == 1
        assert "refused:" in result.err

    @pytest.mark.parametrize("failure", list(WRITE_FAILURES))
    def test_any_error_during_uninstall_after_a_step_has_its_own_exit_status_and_names_the_step_and_the_way_on(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch, failure: str
    ) -> None:
        world = fast_world
        world.install()
        build_error, cause = WRITE_FAILURES[failure]

        def failing_removal(record):
            raise build_error(world.adapter, str(world.state_dir))

        monkeypatch.setattr(world.adapter, "remove_runtime_state", failing_removal)

        result = world.run("uninstall")

        assert result.status == world.adapter.PARTIAL_WRITE_EXIT_STATUS
        assert "failed after writes began" in result.err
        assert "after-generated-files" in result.err
        assert cause in result.err
        assert "Re-run `uninstall`" in result.err
        assert "hooks.json is already handled" in result.err
        assert "leave the saved pre-install file in place" in result.err
        assert "by hand as the Recovery section" not in result.err

    def test_an_error_that_is_no_oserror_after_the_first_install_write_ends_with_the_partial_write_status(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        real_write = world.adapter.durable_write

        def failing_write(path, data, mode=0o600):
            if os.path.basename(path) == "manifest.json":
                raise RuntimeError("boom")
            return real_write(path, data, mode)

        monkeypatch.setattr(world.adapter, "durable_write", failing_write)

        result = world.install()

        assert result.status == world.adapter.PARTIAL_WRITE_EXIT_STATUS
        assert "after-hooks-json" in result.err
        assert "RuntimeError" in result.err
        assert "Traceback" not in result.err

    @pytest.mark.parametrize("failure", ["oserror", "runtime-error"])
    def test_an_error_before_the_first_uninstall_write_is_a_refusal_with_status_one_naming_the_file(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch, failure: str
    ) -> None:
        world = fast_world
        world.hooks_path.write_bytes(ORIGINAL_HOOKS)
        world.install()
        before = world.snapshot()
        build_error, cause = WRITE_FAILURES[failure]

        def failing_write(path, data, mode=0o600):
            error = build_error(world.adapter, path)
            error.filename = path
            raise error

        monkeypatch.setattr(world.adapter, "durable_write", failing_write)

        result = world.run("uninstall")

        assert result.status == 1
        assert str(world.hooks_path) in result.err
        assert "before `uninstall` wrote anything" in result.err
        assert cause in result.err
        assert "failed after writes began" not in result.err
        assert world.snapshot() == before

    def test_a_directory_fsync_that_fails_after_the_rename_landed_is_reported_as_unconfirmed_not_as_a_failed_write(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world

        def failing_fsync(directory):
            raise OSError(errno.EIO, "Input/output error", directory)

        monkeypatch.setattr(world.adapter, "fsync_directory", failing_fsync)

        result = world.install()

        assert result.status == 0, result.err
        assert f"written, durability unconfirmed: {world.hooks_path.parent} (cause EIO)" in result.out
        assert world.adapter.ownership(hooks_document(world)["hooks"]["preToolUse"][0]) == "owned"

    def test_temp_files_a_killed_write_left_are_removed_by_install_and_uninstall_and_a_record_directory_that_stays_is_named(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()
        for directory in (world.cursor_dir, world.record_dir):
            (directory / ".adapter-tmp-leftover").write_text("x")

        world.install()

        assert not (world.cursor_dir / ".adapter-tmp-leftover").exists()
        assert not (world.record_dir / ".adapter-tmp-leftover").exists()
        (world.record_dir / "keep.txt").write_text("not the adapter's")
        (world.record_dir / ".adapter-tmp-again").write_text("x")

        result = world.run("uninstall")

        assert not (world.record_dir / ".adapter-tmp-again").exists()
        assert f"Left {world.record_dir} in place" in result.out
        assert (world.record_dir / "keep.txt").exists()

    def test_an_edit_between_uninstalls_read_and_its_rename_aborts_the_rename_and_keeps_the_edit(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        world.install()
        real_entries = world.adapter.entries_for_uninstall
        edited = {"version": 1, "hooks": {"stop": [{"command": "edited"}]}}

        def editing_entries():
            outcome = real_entries()
            world.hooks_path.write_text(json.dumps(edited))
            return outcome

        monkeypatch.setattr(world.adapter, "entries_for_uninstall", editing_entries)

        result = world.run("uninstall")

        assert result.status == 1
        assert "changed while `uninstall` ran" in result.err
        assert json.loads(world.hooks_path.read_text()) == edited

    @pytest.mark.parametrize("pre_image", [b"[]", b'{"hooks": []}', b'{"hooks": {"stop": {}}}', b"not json"])
    def test_a_pre_install_file_that_is_not_an_object_of_event_arrays_is_treated_as_holding_nothing(
        self, fast_world: World, pre_image: bytes
    ) -> None:
        world = fast_world
        world.install()
        (world.record_dir / "hooks.json.pre-install").write_bytes(pre_image)
        (world.record_dir / "hooks.json.pre-install.none").unlink(missing_ok=True)
        world.hooks_path.write_text(json.dumps({"version": 1, "hooks": {"stop": [{"command": "echo mine"}]}}))

        installed = world.install()
        uninstalled = world.run("uninstall")

        assert installed.status == 0, installed.err
        assert "kept non-owned entry the pre-install file does not hold: stop [0]" in installed.out
        assert uninstalled.status == 0, uninstalled.err


# --- targets the commands write ---


class TestWriteTargetSafety:
    TARGETS = ("cursor-dir", "record-dir", "hooks-file", "state-dir")

    def _path(self, world: World, target: str) -> Path:
        return {
            "cursor-dir": world.cursor_dir,
            "record-dir": world.record_dir,
            "hooks-file": world.hooks_path,
            "state-dir": world.state_dir,
        }[target]

    @pytest.mark.parametrize("target", TARGETS)
    @pytest.mark.parametrize("mode", [0o775, 0o777, 0o757])
    def test_install_refuses_a_target_writable_by_group_or_others_and_writes_nothing(
        self, fast_world: World, target: str, mode: int
    ) -> None:
        world = fast_world
        world.install()
        path = self._path(world, target)
        path.chmod(mode if path.is_dir() else mode & 0o666)
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert str(path) in result.err
        assert "not writable by group or others" in result.err
        assert world.snapshot() == before

    @pytest.mark.parametrize("target", TARGETS)
    @pytest.mark.parametrize("mode", [0o700, 0o755])
    def test_install_accepts_a_target_the_engineer_owns_that_group_and_others_cannot_write(
        self, fast_world: World, target: str, mode: int
    ) -> None:
        world = fast_world
        world.install()
        path = self._path(world, target)
        path.chmod(mode if path.is_dir() else mode & 0o666 & ~0o022)

        result = world.install()

        assert result.status == 0, result.err

    @pytest.mark.parametrize("target", ["cursor-dir", "record-dir", "hooks-file"])
    def test_uninstall_refuses_a_target_writable_by_others_and_writes_nothing(self, fast_world: World, target: str) -> None:
        world = fast_world
        world.install()
        path = self._path(world, target)
        path.chmod(0o777 if path.is_dir() else 0o666)
        before = world.snapshot()

        result = world.run("uninstall")

        assert result.status == 1
        assert str(path) in result.err
        assert world.snapshot() == before

    @pytest.mark.parametrize("target", TARGETS)
    def test_a_target_owned_by_someone_else_is_refused_naming_it(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch, target: str
    ) -> None:
        world = fast_world
        world.install()
        path = self._path(world, target)
        other_uid = os.getuid() + 1

        with monkeypatch.context() as patched:
            patched.setattr(world.adapter.os, "getuid", lambda: other_uid)
            with pytest.raises(world.adapter.AdapterError, match="owned by you") as refusal:
                world.adapter.refuse_unsafe_targets(str(path))

        assert str(path) in str(refusal.value)
        world.adapter.refuse_unsafe_targets(str(path))

    def test_a_missing_target_is_not_a_refusal(self, world: World) -> None:
        world.adapter.refuse_unsafe_targets(str(world.tmp_path / "absent"))

    def test_install_refuses_a_state_dir_that_is_a_link_or_a_file_and_writes_nothing(self, fast_world: World) -> None:
        world = fast_world
        elsewhere = world.tmp_path / "elsewhere"
        elsewhere.mkdir()
        for make_state in (lambda: world.state_dir.symlink_to(elsewhere), lambda: world.state_dir.write_text("x")):
            make_state()
            before = world.snapshot()

            result = world.install()

            assert result.status == 1
            assert str(world.state_dir) in result.err
            assert world.snapshot() == before
            world.state_dir.unlink()

    def test_uninstall_leaves_the_state_dir_when_the_routed_config_dir_no_longer_validates(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        world.config_dir.chmod(0o777)

        result = world.run("uninstall")

        assert result.status == 0, result.err
        assert f"Left {world.state_dir} in place" in result.out
        assert "The engineer deletes it by hand." in result.out
        assert "--config-dir" not in result.out
        assert world.state_dir.is_dir()

    @pytest.mark.parametrize("make_state", ("file", "link"))
    def test_uninstall_leaves_a_state_dir_that_is_not_a_real_directory_and_says_the_engineer_deletes_it_by_hand(
        self, fast_world: World, make_state: str
    ) -> None:
        world = fast_world
        world.install()
        shutil.rmtree(world.state_dir)
        if make_state == "file":
            world.state_dir.write_text("x")
        else:
            elsewhere = world.tmp_path / "elsewhere"
            elsewhere.mkdir()
            world.state_dir.symlink_to(elsewhere)

        result = world.run("uninstall")

        assert result.status == 0, result.err
        left_line = next(line for line in result.out.splitlines() if line.startswith("Left "))
        assert f"Left {world.state_dir} in place" in left_line
        assert left_line.endswith("The engineer deletes it by hand.")
        assert "re-run" not in result.out
        assert "Fix its" not in result.out
        assert world.state_dir.is_symlink() or world.state_dir.is_file()

    def test_uninstall_leaves_a_group_writable_state_dir_and_says_the_engineer_deletes_it_by_hand(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()
        world.state_dir.chmod(0o770)

        result = world.run("uninstall")

        assert result.status == 0, result.err
        left_line = next(line for line in result.out.splitlines() if line.startswith("Left "))
        assert f"Left {world.state_dir} in place" in left_line
        assert left_line.endswith("The engineer deletes it by hand.")
        assert "re-run" not in result.out
        assert "Fix its" not in result.out
        assert world.state_dir.is_dir()

    def test_uninstall_after_the_routed_config_dir_was_deleted_reports_a_clean_uninstall_with_no_install_instruction(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()
        shutil.rmtree(world.config_dir)

        result = world.run("uninstall")

        assert result.status == 0, result.err
        assert result.out.strip().splitlines()[-1].startswith("Uninstalled.")
        assert "remain" not in result.out
        assert "--config-dir" not in result.out
        assert "Left " not in result.out

    def test_an_optional_gh_is_recorded_last_and_judged_by_the_same_rule(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        write_sh_script(world.bin_dir / "gh", "exit 0\n")

        assert world.install().status == 0

        tool_paths = world.install_record()["tool_paths"]
        assert list(tool_paths)[-1] == "gh"
        assert tool_paths["gh"] == str(world.bin_dir / "gh")
        world.run("uninstall")
        working = world.tmp_path / "working"
        (working / "reldir").mkdir(parents=True)
        write_sh_script(working / "reldir" / "gh", "exit 0\n")
        (world.bin_dir / "gh").unlink()
        monkeypatch.chdir(working)
        monkeypatch.setenv("PATH", f"reldir:{world.bin_dir}")
        before = world.snapshot()

        refused = world.install()

        assert refused.status == 1
        assert world.adapter.TOOL_PATH_CONDITION_TEXT[0] in refused.err
        assert world.snapshot() == before


# --- the refusals of validate_config_directory ---


class TestConfigDirectoryRefusals:
    def _directory(self, world: World, name: str, mode: int = 0o700) -> Path:
        directory = world.tmp_path / name
        directory.mkdir(mode=mode)
        directory.chmod(mode)
        return directory

    def test_a_real_directory_you_own_outside_any_repository_is_accepted_as_named(self, world: World) -> None:
        directory = self._directory(world, "accepted")

        assert world.adapter.validate_config_directory(str(directory)) == str(directory)

    def test_a_symlink_to_a_valid_directory_is_refused_as_a_symlink(self, world: World) -> None:
        link = world.tmp_path / "link"
        link.symlink_to(self._directory(world, "real"))

        with pytest.raises(world.adapter.AdapterError, match="is a symlink"):
            world.adapter.validate_config_directory(str(link))

    def test_a_regular_file_is_refused_as_not_a_directory(self, world: World) -> None:
        file_path = world.tmp_path / "plain-file"
        file_path.write_text("x")

        with pytest.raises(world.adapter.AdapterError, match="is not a directory"):
            world.adapter.validate_config_directory(str(file_path))

    def test_a_path_that_does_not_exist_is_refused_as_absent_with_the_way_to_fix_it(self, world: World) -> None:
        with pytest.raises(world.adapter.AdapterError, match=r"does not exist\. Create it, or name an existing directory\."):
            world.adapter.validate_config_directory(str(world.tmp_path / "absent"))

    def test_a_directory_owned_by_someone_else_is_refused_as_not_owned_by_you(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        directory = self._directory(world, "foreign-owner")
        other_uid = os.getuid() + 1
        monkeypatch.setattr(world.adapter.os, "getuid", lambda: other_uid)

        with pytest.raises(world.adapter.AdapterError, match="must be owned by you and not writable by group or others"):
            world.adapter.validate_config_directory(str(directory))

    @pytest.mark.parametrize("mode", [0o770, 0o707], ids=["group-writable", "other-writable"])
    def test_a_directory_writable_by_group_or_others_is_refused_with_the_ownership_text(self, world: World, mode: int) -> None:
        directory = self._directory(world, "writable", mode)

        with pytest.raises(world.adapter.AdapterError, match="must be owned by you and not writable by group or others"):
            world.adapter.validate_config_directory(str(directory))

    def test_a_real_directory_with_a_space_in_its_path_is_refused_by_the_character_rule_and_not_as_unreadable(
        self, world: World
    ) -> None:
        spaced = self._directory(world, "has space")

        with pytest.raises(world.adapter.AdapterError, match="letters, digits and") as refusal:
            world.adapter.validate_config_directory(str(spaced))

        assert "cannot be read" not in str(refusal.value)

    @pytest.mark.parametrize("spelling", ["trailing-slash", "double-slash", "parent-component"])
    def test_a_real_directory_spelled_with_an_empty_or_parent_component_is_refused_and_its_clean_spelling_is_accepted(
        self, world: World, spelling: str
    ) -> None:
        real = self._directory(world, "real-config")
        spellings = {
            "trailing-slash": f"{real}/",
            "double-slash": f"{real.parent}//{real.name}",
            "parent-component": f"{real}/../{real.name}",
        }
        assert world.adapter.validate_config_directory(str(real)) == str(real)

        with pytest.raises(world.adapter.AdapterError, match="no `..` and no empty component") as refusal:
            world.adapter.validate_config_directory(spellings[spelling])

        assert "cannot be read" not in str(refusal.value)

    def test_a_directory_inside_a_git_work_tree_is_refused_as_inside_one(self, world: World) -> None:
        repository = self._directory(world, "repository")
        (repository / ".git").mkdir()
        inside = repository / "config"
        inside.mkdir(mode=0o700)

        with pytest.raises(world.adapter.AdapterError, match="lies inside a git work tree"):
            world.adapter.validate_config_directory(str(inside))


# --- a shim that does not load ---


SHIM_LOAD_FAILURES = [
    ("def broken(:\n", "SyntaxError"),
    ("raise SystemExit(3)\n", "SystemExit"),
    ("raise NameError('x')\n", "NameError"),
]


class TestUnloadableShimReports:
    @pytest.mark.parametrize(("shim_body", "failure_class"), SHIM_LOAD_FAILURES, ids=[row[1] for row in SHIM_LOAD_FAILURES])
    @pytest.mark.parametrize("surface", SURFACES)
    def test_a_shim_that_does_not_load_is_a_finding_on_every_report_and_the_other_findings_still_print(
        self, fast_world: World, surface: str, shim_body: str, failure_class: str
    ) -> None:
        world = fast_world
        world.install()
        wrapper = world.bin_dir / wrapper_name()
        wrapper.unlink()
        load_adapter_copy_with(world, "hook-shim.py", shim_body)

        result = run_surface(world, surface)

        assert f"hook-shim.py did not load ({failure_class})" in result.out
        assert f"The comparison failed ({failure_class})." in result.out
        assert "Traceback" not in result.err
        if surface != "status":
            # Without the shim the cause is the error's class, not its errno symbol.
            assert f"{wrapper} could not be started (cause FileNotFoundError)" in result.out
        assert result.status == (0 if surface == HOOK_ENV_SURFACE else 1)

    @pytest.mark.parametrize(("shim_body", "failure_class"), SHIM_LOAD_FAILURES, ids=[row[1] for row in SHIM_LOAD_FAILURES])
    @pytest.mark.parametrize("command", ["install", "generate"])
    def test_a_command_that_needs_the_shim_refuses_with_the_checkout_fix_and_writes_nothing(
        self, fast_world: World, command: str, shim_body: str, failure_class: str
    ) -> None:
        world = fast_world
        load_adapter_copy_with(world, "hook-shim.py", shim_body)
        before = world.snapshot()

        result = world.install() if command == "install" else world.run("generate")

        assert result.status == 1
        assert f"hook-shim.py did not load ({failure_class}), so `{command}` cannot run. " in result.err
        if command == "install":
            assert result.err.rstrip().endswith(world.adapter.SELF_CHECK_FIX_SENTENCE)
        else:
            assert result.err.rstrip().endswith(world.adapter.GENERATE_RESTORE_SENTENCE)
            assert "re-run `install`" not in result.err
        assert "Traceback" not in result.err
        assert result.out == ""
        assert world.snapshot() == before

    def test_the_hook_env_report_says_it_wrote_no_log_line_when_the_shim_does_not_load(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        load_adapter_copy_with(world, "hook-shim.py", "def broken(:\n")

        result = run_surface(world, HOOK_ENV_SURFACE)

        assert "wrote no log line, first-call mark or rotation" in result.out
        assert not (world.state_dir / "shim.log").exists()

    def test_the_same_reports_with_a_loadable_shim_name_no_load_failure(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        for surface in SURFACES:
            assert "did not load" not in run_surface(world, surface).out, surface


# --- a manifest key that is not a clean path ---


class TestManifestKeys:
    def _install_with_manifest(self, world: World, files: dict[str, str]) -> None:
        world.hooks_path.write_bytes(ORIGINAL_HOOKS)
        world.install()
        (world.record_dir / "manifest.json").write_text(json.dumps({"files": files}))

    def _assert_uninstall_finished(self, world: World, result: Result) -> None:
        assert result.status == 0, result.err
        assert "Traceback" not in result.err
        assert not world.record_dir.exists()
        assert world.hooks_path.read_bytes() == ORIGINAL_HOOKS

    @pytest.mark.parametrize(
        ("key_text", "printed_escape", "raw_text"),
        [
            ("/outside-the-fixed-directories/plain.md", None, None),
            ("/outside/a\x00b.md", "\\x00", "\x00"),
            ("/outside/\x1b[31m\x85red.md", "\\x1b", "\x1b"),
        ],
        ids=["plain", "nul", "control-bytes"],
    )
    def test_a_manifest_key_is_kept_and_printed_escaped_and_the_remaining_uninstall_steps_run(
        self, fast_world: World, key_text: str, printed_escape: str | None, raw_text: str | None
    ) -> None:
        world = fast_world
        self._install_with_manifest(world, {key_text: hashlib.sha256(b"generated").hexdigest()})

        result = world.run("uninstall")

        self._assert_uninstall_finished(world, result)
        assert "Kept " in result.out
        if printed_escape is not None:
            assert printed_escape in result.out
            assert raw_text not in result.out

    def test_ten_thousand_manifest_keys_print_twenty_lines_and_a_count_of_the_rest(self, fast_world: World) -> None:
        world = fast_world
        digest = hashlib.sha256(b"generated").hexdigest()
        count = 10_000
        self._install_with_manifest(world, {f"/outside/{number}.md": digest for number in range(count)})

        result = world.run("uninstall")

        self._assert_uninstall_finished(world, result)
        shown = world.adapter.PRINT_SET_MAX_ENTRIES
        assert result.out.count("Kept ") == shown
        assert f"{count - shown} more kept manifest path(s) not shown" in result.out


# --- reads of planted files and of deeply nested output ---


def plant(path: Path, kind: str, regular_content: bytes) -> None:
    """Replaces `path` with a FIFO or with a regular file holding `regular_content`."""
    path.unlink(missing_ok=True)
    if kind == "fifo":
        os.mkfifo(path)
    else:
        path.write_bytes(regular_content)


def run_without_blocking(world: World, *argv: str, seconds: int = 30) -> Result:
    """`world.run` on a thread, so a read that blocks on a FIFO fails the test and does not hang the run."""
    outcome: dict[str, Result] = {}
    thread = threading.Thread(target=lambda: outcome.update(result=world.run(*argv)), daemon=True)
    thread.start()
    thread.join(seconds)
    assert not thread.is_alive(), f"{argv} blocked on a FIFO"
    return outcome["result"]


class TestPlantedFilesAndNesting:
    @pytest.mark.parametrize("kind", ["regular", "fifo"])
    def test_status_reads_a_regular_log_and_skips_a_fifo_where_the_log_should_be(self, fast_world: World, kind: str) -> None:
        world = fast_world
        world.install()
        plant(world.state_dir / "shim.log", kind, b"reg=require-code-review out=ok\n")

        result = run_without_blocking(world, "status")

        assert result.status == 0
        assert "recorded python3:" in result.out
        assert ("registration require-code-review: 1 call(s)" in result.out) is (kind == "regular")

    @pytest.mark.parametrize("kind", ["regular", "fifo"])
    def test_check_reads_a_regular_install_record_and_calls_a_fifo_there_unusable(self, fast_world: World, kind: str) -> None:
        world = fast_world
        world.install()
        plant(world.record_dir / "install.json", kind, (world.record_dir / "install.json").read_bytes())

        result = run_without_blocking(world, "check")

        assert ("No usable install record" in result.out) is (kind == "fifo")
        assert result.status == (1 if kind == "fifo" else 0)

    @pytest.mark.parametrize("kind", ["regular", "fifo"])
    def test_uninstall_reads_a_regular_manifest_and_calls_a_fifo_there_missing(self, fast_world: World, kind: str) -> None:
        world = fast_world
        world.install()
        outside = f"{world.tmp_path}/outside.md"
        plant(world.record_dir / "manifest.json", kind, json.dumps({"files": {outside: "0" * 64}}).encode())

        result = run_without_blocking(world, "uninstall")

        assert result.status == 0, result.err
        assert (f"Kept {outside}" in result.out) is (kind == "regular")
        assert ("is missing or unreadable, so no generated file was removed" in result.out) is (kind == "fifo")

    def test_a_self_check_result_nested_past_the_parsers_limit_gives_no_result_and_a_well_formed_one_gives_one(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        python_path, wrapper = str(world.bin_dir / "python3"), str(world.bin_dir / wrapper_name())
        interpreter = dict.fromkeys(world.adapter.SELF_CHECK_INTERPRETER_KEYS, 1)
        well_formed = json.dumps({"interpreter": interpreter, "failures": []}).encode()

        def run_returning(output: bytes):
            return lambda *args, **keywords: types.SimpleNamespace(returncode=0, stdout=output, stderr=b"")

        monkeypatch.setattr(world.adapter.subprocess, "run", run_returning(well_formed))
        assert world.adapter.run_self_check(python_path, wrapper).failures == []
        monkeypatch.setattr(world.adapter.subprocess, "run", run_returning(b"[" * 500_000))

        with pytest.raises(world.adapter.AdapterError, match="gave no result"):
            world.adapter.run_self_check(python_path, wrapper)

    @pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("self-check", 7), OSError(errno.ENOEXEC, "bad")], ids=type)
    def test_a_self_check_that_times_out_says_to_re_run_once_and_a_start_failure_does_not(
        self, world: World, monkeypatch: pytest.MonkeyPatch, failure: Exception
    ) -> None:
        def raising_run(*args, **keywords):
            raise failure

        monkeypatch.setattr(world.adapter.subprocess, "run", raising_run)

        with pytest.raises(world.adapter.AdapterError, match="gave no result") as refusal:
            world.adapter.run_self_check(str(world.bin_dir / "python3"), str(world.bin_dir / wrapper_name()))

        said_to_re_run = "Re-run the command once" in str(refusal.value)
        assert said_to_re_run is isinstance(failure, subprocess.TimeoutExpired)
        python_path, wrapper = str(world.bin_dir / "python3"), str(world.bin_dir / wrapper_name())
        repeat = shlex.join([python_path, "-I", "-S", world.adapter.stow_adapter_path(), "self-check", "--wrapper", wrapper])
        assert f"run `{repeat}` from a terminal" in str(refusal.value)
        assert (world.adapter.SELF_CHECK_FIX_SENTENCE in str(refusal.value)) is isinstance(failure, OSError)
        shim = world.adapter.load_shim()
        bound = shim.entry_timeout_seconds(shim.MIN_DEADLINE_SECONDS)
        assert (f"within {bound} s" in str(refusal.value)) is isinstance(failure, subprocess.TimeoutExpired)

    @pytest.mark.parametrize(
        ("returncode", "stdout"),
        [(3, b""), (0, b"not json"), (0, b"[]"), (0, b'{"interpreter": {}, "failures": "x"}')],
        ids=["nonzero-exit", "unparseable", "wrong-shape", "failures-not-a-list"],
    )
    def test_a_self_check_that_gives_no_result_ends_with_the_checkout_fix_and_the_command_that_repeats_the_call(
        self, world: World, monkeypatch: pytest.MonkeyPatch, returncode: int, stdout: bytes
    ) -> None:
        python_path, wrapper = str(world.bin_dir / "python3"), str(world.bin_dir / wrapper_name())
        monkeypatch.setattr(
            world.adapter.subprocess,
            "run",
            lambda *args, **keywords: types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=b""),
        )

        with pytest.raises(world.adapter.AdapterError, match="gave no result") as refusal:
            world.adapter.run_self_check(python_path, wrapper)

        repeat = shlex.join([python_path, "-I", "-S", world.adapter.stow_adapter_path(), "self-check", "--wrapper", wrapper])
        assert world.adapter.SELF_CHECK_FIX_SENTENCE in str(refusal.value)
        assert str(refusal.value).endswith(f"To see the cause, run `{repeat}` from a terminal.")

    def test_uninstall_without_a_loadable_shim_refuses_a_hooks_file_it_cannot_write_back_and_writes_nothing(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()
        text = world.hooks_path.read_text()
        world.hooks_path.write_text(text.rstrip().removesuffix("}") + ', "limit": NaN}')
        load_adapter_copy_with(world, "hook-shim.py", "def broken(:\n")
        before = world.snapshot()

        result = world.run("uninstall")

        assert result.status == 1
        assert "refused:" in result.err
        assert str(world.hooks_path) in result.err
        assert "Traceback" not in result.err
        assert world.snapshot() == before


# --- shapes that fail before the first write ---


SETTINGS_SHAPES = {
    "hooks-is-a-list": {"hooks": []},
    "event-is-an-object": {"hooks": {"PreToolUse": {}}},
    "group-is-a-string": {"hooks": {"PreToolUse": ["x"]}},
    "group-hooks-is-an-object": {"hooks": {"PreToolUse": [{"hooks": {}}]}},
    "hook-is-a-string": {"hooks": {"PreToolUse": [{"hooks": ["x"]}]}},
}


def use_settings(world: World, monkeypatch: pytest.MonkeyPatch, document: dict) -> Path:
    settings = world.tmp_path / "shaped-settings.json"
    settings.write_text(json.dumps(document))
    monkeypatch.setattr(world.adapter, "settings_json_path", lambda: str(settings))
    return settings


class TestFailuresBeforeTheFirstWrite:
    @pytest.mark.parametrize("command", ["install", "uninstall"])
    @pytest.mark.parametrize("shape", ["hooks-is-a-directory", "hooks-mode-000", "cursor-dir-is-a-file"])
    def test_an_unreadable_target_is_one_refusal_naming_the_path_with_nothing_written(
        self, fast_world: World, command: str, shape: str
    ) -> None:
        world = fast_world
        unreadable = shape == "hooks-mode-000"
        if unreadable and IS_ROOT:
            pytest.skip("absent: root reads a mode-000 file")
        if shape == "hooks-is-a-directory":
            world.hooks_path.mkdir()
        elif unreadable:
            world.hooks_path.write_bytes(ORIGINAL_HOOKS)
            world.hooks_path.chmod(0o600)
        else:
            shutil.rmtree(world.cursor_dir)
            world.cursor_dir.write_text("not a directory")
        before = world.snapshot()
        if unreadable:
            world.hooks_path.chmod(0)

        result = world.install() if command == "install" else world.run("uninstall")

        if unreadable:
            world.hooks_path.chmod(0o600)
        named = world.cursor_dir if shape == "cursor-dir-is-a-file" else world.hooks_path
        assert result.status == 1
        assert str(named) in result.err
        assert "Traceback" not in result.err
        assert world.snapshot() == before

    @pytest.mark.parametrize("surface", SURFACES)
    def test_a_hooks_file_that_cannot_be_read_is_a_finding_and_the_other_findings_still_print(
        self, fast_world: World, surface: str
    ) -> None:
        world = fast_world
        world.install()
        (world.bin_dir / wrapper_name()).unlink()
        world.hooks_path.unlink()
        world.hooks_path.mkdir()

        result = run_surface(world, surface)

        assert f"{world.hooks_path} cannot be read" in result.out
        if surface != "status":
            assert "could not be started (cause ENOENT)" in result.out
        assert result.status == (1 if surface == "check" else 0)

    @pytest.mark.parametrize("shape", list(SETTINGS_SHAPES))
    def test_a_settings_file_whose_hooks_member_has_the_wrong_shape_refuses_install_naming_it_with_nothing_written(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch, shape: str
    ) -> None:
        world = fast_world
        settings = use_settings(world, monkeypatch, SETTINGS_SHAPES[shape])
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert str(settings) in result.err
        assert "Traceback" not in result.err
        assert world.snapshot() == before

    def test_a_settings_file_with_no_registrations_installs_so_the_shape_rows_fail_on_the_shape(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        use_settings(world, monkeypatch, {"hooks": {}})

        assert world.install().status == 0

    @pytest.mark.parametrize("shape", list(SETTINGS_SHAPES))
    def test_uninstall_goes_on_without_the_expected_entries_when_settings_has_the_wrong_shape(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch, shape: str
    ) -> None:
        world = fast_world
        world.hooks_path.write_bytes(ORIGINAL_HOOKS)
        world.install()
        use_settings(world, monkeypatch, SETTINGS_SHAPES[shape])
        check_cause = re.search(r"The comparison failed \((AdapterError: .*)\)\.", world.run("check").out)
        assert check_cause, "check names the failed comparison with the AdapterError's message"

        result = world.run("uninstall")

        assert result.status == 0, result.err
        assert f"generating the expected entries failed ({check_cause.group(1)})" in result.out
        assert world.hooks_path.read_bytes() == ORIGINAL_HOOKS


def exploding_callee(*args, **keywords):
    raise RuntimeError("SECRET-MESSAGE")


class TestAnUnexpectedExceptionEndsAsOneLine:
    def _assert_one_line_exit_one(self, result: Result, command: str) -> str:
        assert result.status == 1
        lines = result.err.splitlines()
        assert len(lines) == 1, result.err
        assert lines[0].startswith(f"`{command}` failed on an unexpected ")
        assert "Traceback" not in result.err
        return lines[0]

    @pytest.mark.parametrize(
        ("command", "callee"),
        [
            ("install", "generate_entries"),
            ("generate", "generate_entries"),
            ("uninstall", "classify_hooks"),
            ("check", "tool_findings"),
            ("status", "read_log"),
        ],
    )
    def test_an_exception_no_command_arm_catches_ends_as_one_line_naming_the_class_and_the_frame_and_no_message(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch, command: str, callee: str
    ) -> None:
        world = fast_world
        world.hooks_path.write_bytes(ORIGINAL_HOOKS)
        if command != "install":
            world.install()
        monkeypatch.setattr(world.adapter, callee, exploding_callee)
        before = world.snapshot()

        result = world.install() if command == "install" else world.run(command)

        line = self._assert_one_line_exit_one(result, command)
        assert line.startswith(f"`{command}` failed on an unexpected RuntimeError at {Path(__file__).name}:")
        assert re.search(rf" in {exploding_callee.__name__}\.", line)
        assert "SECRET-MESSAGE" not in result.err
        assert line.endswith(" Nothing was written.") is (command in ("install", "uninstall"))
        assert world.snapshot() == before

    def test_install_over_a_settings_matcher_that_is_not_a_string_ends_as_one_line_with_nothing_written(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        use_settings(
            world,
            monkeypatch,
            {"hooks": {"PreToolUse": [{"matcher": 7, "hooks": [{"type": "command", "command": "/x/hooks/gate.sh"}]}]}},
        )
        before = world.snapshot()

        result = world.install()

        self._assert_one_line_exit_one(result, "install")
        assert world.snapshot() == before

    def test_install_with_a_parity_entry_that_has_no_string_key_ends_as_one_line_with_nothing_written(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        load_adapter_copy_with(world, "parity-exceptions.json", json.dumps({"entries": [{"kind": "registration"}]}))
        monkeypatch.setattr(world.adapter, "settings_json_path", lambda: str(CLAUDE_DIR / "settings.json"))
        before = world.snapshot()

        result = world.install()

        self._assert_one_line_exit_one(result, "install")
        assert world.snapshot() == before


# --- a pre-install file uninstall cannot use ---


class TestUnusablePreInstallFile:
    @pytest.mark.parametrize(
        "pre_image", [b'{"hooks": []}', b'{"hooks": null}', b'{"hooks": {"stop": {}}}', b"[]", b"not json"], ids=repr
    )
    def test_uninstall_skips_the_restore_from_a_pre_install_file_whose_hooks_is_not_an_object_of_arrays_and_runs_the_rest(
        self, fast_world: World, pre_image: bytes
    ) -> None:
        world = fast_world
        world.hooks_path.write_bytes(ORIGINAL_HOOKS)
        world.install()
        (world.record_dir / "hooks.json.pre-install").write_bytes(pre_image)

        result = world.run("uninstall")

        assert result.status == 0, result.err
        assert "Traceback" not in result.err
        assert "the saved pre-install file is not a usable hooks.json" in result.out
        assert not world.record_dir.exists()
        assert hooks_document(world)["hooks"] == {"stop": [{"command": "echo mine"}]}

    def test_the_two_shape_readers_agree_on_which_pre_install_bytes_are_usable(self, world: World) -> None:
        usable = b'{"version": 1, "hooks": {"stop": [{"command": "x"}]}}'
        assert world.adapter.pre_image_document_of(usable) is not None
        assert world.adapter.pre_image_hooks_of(usable) == {"stop": [{"command": "x"}]}
        for unusable in (b'{"hooks": []}', b'{"hooks": {"stop": {}}}', b"[]", b"not json", None):
            assert world.adapter.pre_image_document_of(unusable) is None
            assert world.adapter.pre_image_hooks_of(unusable) == {}


# --- a hooks file whose version the adapter does not write ---


class TestHooksFileVersion:
    @pytest.mark.parametrize(
        ("content", "found"),
        [
            (b'{"hooks": {}}', "absent"),
            (b'{"version": 2, "hooks": {}}', "2"),
            (b'{"version": "1", "hooks": {}}', '"1"'),
            (b'{"version": true, "hooks": {}}', "true"),
            (b"{}", "absent"),
        ],
        ids=["no-version", "version-two", "string-one", "boolean", "empty-object"],
    )
    def test_install_refuses_a_hooks_file_whose_version_is_not_one_naming_the_file_and_the_version_with_nothing_written(
        self, fast_world: World, content: bytes, found: str
    ) -> None:
        world = fast_world
        world.hooks_path.write_bytes(content)
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert str(world.hooks_path) in result.err
        assert f"top-level version {found}," in result.err
        assert world.adapter.VERSION_FIX_SENTENCE in result.err
        assert world.snapshot() == before

    def test_install_over_a_version_one_file_succeeds_and_check_agrees(self, fast_world: World) -> None:
        world = fast_world
        world.hooks_path.write_bytes(b'{"version": 1, "hooks": {}}\n')

        assert world.install().status == 0
        assert "the top-level version" not in world.run("check").out


# --- a record the shim would refuse ---


RECORD_DAMAGE = {
    "tool-map-without-a-required-tool": lambda record: record["tool_map"]["tools"].pop("Shell"),
    "tool-map-not-an-object": lambda record: record.update(tool_map=[]),
    "tool-map-unknown-translation": lambda record: record["tool_map"]["tools"]["Shell"].update(translation="invented"),
    "no-wrapper": lambda record: [record["tool_paths"].pop(name, None) for name in ("timeout", "gtimeout")],
}


class TestRecordTheShimRefuses:
    @pytest.mark.parametrize("surface", SURFACES)
    @pytest.mark.parametrize("damage", list(RECORD_DAMAGE))
    def test_a_record_the_shims_reader_refuses_is_a_finding_on_every_report(
        self, fast_world: World, surface: str, damage: str
    ) -> None:
        world = fast_world
        world.install()
        shim = world.adapter.load_shim()
        record = world.install_record()
        RECORD_DAMAGE[damage](record)
        (world.record_dir / "install.json").write_text(json.dumps(record))
        with pytest.raises(shim.Refusal):
            shim.read_install_record(shim.CallState(), require_wrapper=True)

        result = run_surface(world, surface)

        assert "The shim refuses the install record" in result.out
        assert result.status == (1 if surface == "check" else 0)

    @pytest.mark.parametrize("surface", SURFACES)
    def test_an_intact_record_gets_no_such_finding(self, fast_world: World, surface: str) -> None:
        world = fast_world
        world.install()

        assert "The shim refuses the install record" not in run_surface(world, surface).out

    def test_install_refuses_a_tool_map_the_shim_would_refuse_naming_the_file_with_nothing_written(
        self, fast_world: World
    ) -> None:
        world = fast_world
        load_adapter_copy_with(world, "tool-map.json", json.dumps({"tools": {}}))
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert f"{world.tmp_path / 'broken'}" in result.err
        assert "tool-map.json is not a tool map the shim accepts" in result.err
        assert world.snapshot() == before


# --- text a report prints beyond a count ---


class TestPrintedGuidance:
    def test_uninstall_ends_with_a_step_that_can_pass_and_not_with_check(self, fast_world: World) -> None:
        world = fast_world
        world.hooks_path.write_bytes(ORIGINAL_HOOKS)
        world.install()

        result = world.run("uninstall")

        last_line = result.out.strip().splitlines()[-1]
        assert last_line.startswith("Uninstalled.")
        assert str(world.hooks_path) in last_line
        assert world.adapter.invocation("check") not in result.out

    def test_uninstall_does_not_say_the_adapter_is_gone_while_a_foreign_entry_or_a_left_state_directory_remains(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        foreign = {"command": world.adapter.stow_shim_path() + " nonsense"}
        world.hooks_path.write_text(json.dumps({"version": 1, "hooks": {"stop": [foreign]}}))
        world.install()
        monkeypatch.setattr(world.adapter, "remove_runtime_state", lambda record: False)

        result = world.run("uninstall")

        last_line = result.out.strip().splitlines()[-1]
        assert result.status == 0
        assert "1 kept foreign entry(s) in hooks.json, the runtime state directory remain" in last_line
        assert "holds no entry that runs the adapter" not in result.out

    def test_check_reports_a_settings_link_that_does_not_resolve_with_the_stow_fix(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        (world.config_dir / "settings.json").unlink()
        (world.config_dir / "settings.json").write_text("{}")

        result = world.run("check")

        tracked = os.path.realpath(world.adapter.settings_json_path())
        assert f"the routed config dir's settings.json does not resolve to {tracked}. " in result.out
        assert f"Re-link {world.config_dir / 'settings.json'} to the checkout's file." in result.out
        assert "`./install.sh` covers only the bare ~/.claude." in result.out
        assert world.adapter.STOW_FIX_SENTENCE not in result.out

    def test_check_reports_a_bare_settings_link_that_does_not_resolve_with_the_stow_fix(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        bare = world.home / ".claude" / "settings.json"
        bare.unlink()
        bare.write_text("{}")

        result = world.run("check")

        tracked = os.path.realpath(world.adapter.settings_json_path())
        assert f"bare ~/.claude/settings.json does not resolve to {tracked}. {world.adapter.STOW_FIX_SENTENCE}" in result.out

    def test_install_does_not_test_the_settings_link_so_the_report_that_follows_it_can_name_one(self, fast_world: World) -> None:
        world = fast_world
        (world.config_dir / "settings.json").unlink()

        assert world.install().status == 0
        assert "does not resolve" in world.run("check").out


# --- fsync ---


class TestFlushToDisk:
    def test_the_full_fsync_is_asked_for_where_the_platform_has_it_and_os_fsync_is_the_fallback(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple] = []
        monkeypatch.setattr(world.adapter.os, "fsync", lambda descriptor: calls.append(("fsync", descriptor)))
        descriptor = os.open(world.tmp_path, os.O_RDONLY)
        try:
            full_fsync = 99
            recording_fcntl = types.SimpleNamespace(
                F_FULLFSYNC=full_fsync, fcntl=lambda handle, operation: calls.append(("fcntl", operation))
            )
            monkeypatch.setattr(world.adapter, "fcntl", recording_fcntl)
            world.adapter.flush_to_disk(descriptor)
            assert calls == [("fcntl", full_fsync)]

            calls.clear()
            monkeypatch.setattr(world.adapter, "fcntl", types.SimpleNamespace())
            world.adapter.flush_to_disk(descriptor)
            assert calls == [("fsync", descriptor)]

            calls.clear()

            def refused(handle, operation):
                raise OSError(errno.ENOTSUP, "not supported")

            monkeypatch.setattr(world.adapter, "fcntl", types.SimpleNamespace(F_FULLFSYNC=full_fsync, fcntl=refused))
            world.adapter.flush_to_disk(descriptor)
            assert calls == [("fsync", descriptor)]
        finally:
            os.close(descriptor)

    def test_a_durable_write_flushes_the_file_and_then_its_directory_through_the_one_function(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        flushed_a_directory: list[bool] = []

        def record_flush(descriptor):
            flushed_a_directory.append(stat.S_ISDIR(os.fstat(descriptor).st_mode))

        monkeypatch.setattr(world.adapter, "flush_to_disk", record_flush)
        target = world.tmp_path / "durable.txt"

        world.adapter.durable_write(str(target), b"data")

        assert target.read_bytes() == b"data"
        assert flushed_a_directory == [False, True]


# --- uninstall with nothing installed ---


NOTHING_INSTALLED_PREFIX = "Nothing was installed"


class TestUninstallWithNothingInstalled:
    @pytest.mark.parametrize("original", [ORIGINAL_HOOKS, None], ids=["engineers-file", "no-file"])
    def test_a_second_uninstall_changes_nothing_prints_no_entry_text_and_ends_on_the_nothing_installed_line(
        self, fast_world: World, original: bytes | None
    ) -> None:
        world = fast_world
        if original is not None:
            world.hooks_path.write_bytes(original)
        world.install()
        assert world.run("uninstall").status == 0
        after_first = world.snapshot()

        result = world.run("uninstall")

        assert result.status == 0, result.err
        lines = result.out.splitlines()
        assert len(lines) == 1, result.out
        assert lines[0].startswith(NOTHING_INSTALLED_PREFIX)
        assert "echo mine" not in result.out
        assert world.snapshot() == after_first
        assert (world.hooks_path.read_bytes() if world.hooks_path.exists() else None) == original
        assert not list(world.cursor_dir.rglob(f"{world.adapter.TEMP_FILE_PREFIX}*"))

    def test_uninstall_on_a_host_that_never_installed_and_has_no_hooks_file_says_so_and_creates_nothing(
        self, fast_world: World
    ) -> None:
        world = fast_world
        assert not world.hooks_path.exists()
        before = world.snapshot()

        result = world.run("uninstall")

        assert result.status == 0, result.err
        lines = result.out.splitlines()
        assert len(lines) == 1, result.out
        assert lines[0].startswith(NOTHING_INSTALLED_PREFIX)
        assert world.snapshot() == before

    def test_a_kept_manifest_path_is_named_in_the_closing_line_and_the_closing_line_does_not_say_uninstalled(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()
        outside = f"{world.tmp_path}/outside.md"
        (world.record_dir / "manifest.json").write_text(json.dumps({"files": {outside: "0" * 64}}))

        result = world.run("uninstall")

        last_line = result.out.strip().splitlines()[-1]
        assert result.status == 0, result.err
        assert f"Kept {outside}" in result.out
        assert last_line.startswith("Uninstalled the adapter's own entries, but not everything:")
        assert "1 kept manifest path(s) remains" in last_line

    def test_a_foreign_entry_alone_ends_the_run_naming_it_and_not_with_the_nothing_installed_line(
        self, fast_world: World
    ) -> None:
        world = fast_world
        foreign = {"command": world.adapter.stow_shim_path() + " nonsense"}
        original = json.dumps({"version": 1, "hooks": {"stop": [foreign]}}).encode()
        world.hooks_path.write_bytes(original)

        result = world.run("uninstall")

        assert result.status == 0, result.err
        assert "1 kept foreign entry(s) in hooks.json remains" in result.out.strip().splitlines()[-1]
        assert NOTHING_INSTALLED_PREFIX not in result.out
        assert "missing or unreadable" not in result.out
        assert "left in place" not in result.out
        assert world.adapter.FOREIGN_FIX_SENTENCE in result.out
        assert world.hooks_path.read_bytes() == original

    def _install_then_add_an_entry(self, world: World) -> None:
        world.hooks_path.write_bytes(ORIGINAL_HOOKS)
        world.install()
        document = hooks_document(world)
        document["hooks"]["stop"].append({"command": "echo added"})
        save_hooks(world, document)

    def test_the_kept_non_owned_entries_the_saved_pre_install_file_lacks_are_listed_when_that_file_exists(
        self, fast_world: World
    ) -> None:
        world = fast_world
        self._install_then_add_an_entry(world)

        result = world.run("uninstall")

        assert result.status == 0, result.err
        assert 'kept non-owned entry the pre-install file does not hold: stop [1]: command="echo added"' in result.out

    def test_no_non_owned_entry_is_listed_when_no_saved_pre_install_file_exists(self, fast_world: World) -> None:
        world = fast_world
        self._install_then_add_an_entry(world)
        (world.record_dir / "hooks.json.pre-install").unlink()

        result = world.run("uninstall")

        assert result.status == 0, result.err
        assert "kept non-owned entry" not in result.out
        assert "echo added" not in result.out


# --- the comparison beside a failed generation ---


# A settings.json registration whose matcher token has no Cursor tool name, so `generate` refuses it.
UNTRANSLATABLE_SETTINGS = {
    "hooks": {"PreToolUse": [{"matcher": "NotebookEdit", "hooks": [{"type": "command", "command": "/x/hooks/gate.sh"}]}]}
}


class TestComparisonBesideAFailedGeneration:
    @pytest.mark.parametrize("surface", SURFACES)
    def test_every_check_that_needs_no_generated_entries_still_reports_after_the_generation_failure(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch, surface: str
    ) -> None:
        world = fast_world
        world.install()
        record = world.install_record()
        record["tool_map"]["tools"]["Extra"] = {"claude": "Extra", "translation": "passthrough"}
        record["contract_version"] = "0"
        (world.record_dir / "install.json").write_text(json.dumps(record))
        document = hooks_document(world)
        document["hooks"].setdefault("stop", []).append({"command": f"{world.adapter.stow_shim_path()} planted"})
        position = len(document["hooks"]["stop"]) - 1
        save_hooks(world, document)
        use_settings(world, monkeypatch, UNTRANSLATABLE_SETTINGS)

        result = run_surface(world, surface)

        failure = "finding: The comparison failed (AdapterError"
        assert failure in result.out
        # The sessionStart report reaches the agent, so its fix names the engineer; the terminal reports tell the reader to act.
        on_session_start = surface == HOOK_ENV_SURFACE
        fix_sentence = world.adapter.AGENT_RESTORE_SENTENCE if on_session_start else world.adapter.RESTORE_CHECKOUT_SENTENCE
        assert fix_sentence in result.out
        assert (world.adapter.RESTORE_CHECKOUT_SENTENCE in result.out) is not on_session_start
        later_findings = [
            "tool-map.json differs from the map the install recorded",
            "contract version differs from the shim's: a stale install",
            f"foreign entry that holds an adapter path: stop [{position}]",
        ]
        for finding in later_findings:
            assert finding in result.out, finding
            assert result.out.index(failure) < result.out.index(finding), finding
        assert result.out.index(fix_sentence) < result.out.index("a stale install")
        assert result.status == (1 if surface == "check" else 0)

    @pytest.mark.parametrize("surface", SURFACES)
    def test_a_failed_generation_beside_a_clean_install_record_reports_only_the_failure(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch, surface: str
    ) -> None:
        world = fast_world
        world.install()
        use_settings(world, monkeypatch, UNTRANSLATABLE_SETTINGS)

        result = run_surface(world, surface)

        assert "finding: The comparison failed (AdapterError" in result.out
        for absent in ("tool-map.json differs", "a stale install", "foreign entry", "owned entry"):
            assert absent not in result.out, absent


# --- the exit status of status ---


class TestStatusExitStatus:
    def test_status_exits_1_without_a_record_or_with_an_unloadable_shim_and_0_with_findings(
        self, fast_world: World
    ) -> None:
        world = fast_world
        assert world.run("status").status == 1
        world.install()
        document = hooks_document(world)
        del document["hooks"]["preToolUse"][0]
        save_hooks(world, document)
        with_findings = world.run("status")
        assert with_findings.status == 0
        assert "finding: " in with_findings.out
        load_adapter_copy_with(world, "hook-shim.py", "def broken(:\n")
        unloadable = world.run("status")
        assert unloadable.status == 1
        assert "hook-shim.py did not load" in unloadable.out


# --- the top-level members finding's way out ---


class TestTopLevelMembersFix:
    def test_the_finding_names_uninstall_then_install_and_clears_after_both(self, fast_world: World) -> None:
        world = fast_world
        world.hooks_path.write_bytes(ORIGINAL_HOOKS)
        world.install()
        document = hooks_document(world)
        document["added"] = 1
        save_hooks(world, document)

        before = world.run("check")

        assert "top-level members of hooks.json that differ from the pre-install file: 1" in before.out
        assert "quit Cursor, run `uninstall`, then re-run `install` and relaunch Cursor" in before.out
        assert world.run("uninstall").status == 0
        assert world.install().status == 0
        after = world.run("check")
        assert after.status == 0, after.out
        assert "top-level" not in after.out


# --- the hand-edit fixes ---


class TestHandEditFixesStartWithQuittingCursor:
    @pytest.mark.parametrize("surface", SURFACES)
    def test_the_foreign_entry_and_version_fixes_start_with_quit_cursor(self, fast_world: World, surface: str) -> None:
        world = fast_world
        world.install()
        document = hooks_document(world)
        document["version"] = 2
        document["hooks"]["stop"] = [{"command": f"{world.adapter.stow_shim_path()} planted"}]
        save_hooks(world, document)

        out = run_surface(world, surface).out

        for fix_sentence in (world.adapter.FOREIGN_FIX_SENTENCE, world.adapter.VERSION_FIX_SENTENCE):
            assert fix_sentence.startswith("Quit Cursor")
            assert fix_sentence in out


# --- a stale contract version and an absent hooks.json on every report ---


class TestStaleContractVersionAndAbsentHooksFile:
    @pytest.mark.parametrize("surface", SURFACES)
    def test_a_shim_whose_contract_version_moved_is_a_stale_install_and_not_a_refused_record(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch, surface: str
    ) -> None:
        world = fast_world
        world.install()
        monkeypatch.setattr(world.adapter.load_shim(), "CONTRACT_VERSION", "2")

        result = run_surface(world, surface)

        assert "contract version differs from the shim's: a stale install" in result.out
        assert "finding: owned entry differs from what generate emits now" in result.out
        assert "The shim refuses the install record" not in result.out
        assert result.status == (1 if surface == "check" else 0)

    @pytest.mark.parametrize("surface", SURFACES)
    def test_an_absent_hooks_file_is_a_finding_with_the_reinstall_fix_and_only_check_exits_nonzero(
        self, fast_world: World, surface: str
    ) -> None:
        world = fast_world
        world.install()
        world.hooks_path.unlink()

        result = run_surface(world, surface)

        assert f"finding: {world.hooks_path} does not exist. {world.adapter.TAMPER_FIX_SENTENCE}" in result.out
        assert result.status == (1 if surface == "check" else 0)


# --- status's canary for a Shell-selecting registration that never fires ---


# The preToolUse registrations that select Shell: the `Bash`-matcher gates of settings.json, written here so the function under
# test is not its own oracle, plus the shim's `pin` and `classify` entries.
LITERAL_SHELL_SELECTING_LABELS = (
    "block-gh-pr-merge",
    "check-claude-md-length",
    "check-skill-length",
    "classify",
    "deny-credential-bash-reads",
    "deny-default-branch-push",
    "deny-escaped-backticks-in-pr-body",
    "deny-invisible-commit-content",
    "deny-network-installs",
    "deny-pii-in-commits",
    "deny-private-project-refs",
    "deny-repo-relocation",
    "deny-reviewer-tree-mutation",
    "enforce-marker-script-shape",
    "guard-settings-session-keys",
    "pin",
    "require-code-review",
    "require-diff-size-prompt",
    "require-ready-for-review",
    "require-respond-pr",
    "require-stow-reminder",
)


class TestShellCanaryFlag:
    FLAG_PREFIX = "flag: the Shell-selecting registration "

    def _silent_shell_selecting_labels(self, world: World) -> set[str]:
        emitted = world.adapter.generate_entries(python_path=sys.executable)
        return set(world.adapter.shell_selecting_registrations_without_a_fire(emitted, {}))

    def test_a_gate_whose_settings_matcher_is_bash_is_named_as_selecting_shell(self, fast_world: World) -> None:
        assert "deny-network-installs" in self._silent_shell_selecting_labels(fast_world)

    @pytest.mark.parametrize("label", ["ask-review-permissions", "deny-env-reads"])
    def test_a_gate_whose_settings_matcher_names_no_shell_tool_is_never_named(self, fast_world: World, label: str) -> None:
        assert label not in self._silent_shell_selecting_labels(fast_world)

    def test_the_named_registrations_are_exactly_the_literal_shell_selecting_ones(self, fast_world: World) -> None:
        assert self._silent_shell_selecting_labels(fast_world) == set(LITERAL_SHELL_SELECTING_LABELS)

    def _selecting_labels(self, world: World) -> list[str]:
        emitted = world.adapter.generate_entries(python_path=sys.executable)
        labels = sorted(set(world.adapter.shell_selecting_registrations_without_a_fire(emitted, {})))
        assert "pin" in labels
        assert "check" not in labels
        assert "pin-check" not in labels
        return labels

    def _flagged(self, world: World) -> list[str]:
        out = world.run("status").out
        matches = (re.match(rf"{self.FLAG_PREFIX}(\S+) has not fired", line) for line in out.splitlines())
        return [match.group(1) for match in matches if match]

    def _log_shell_calls(self, world: World, labels: list[str]) -> None:
        write_raw_log(world, "".join(f"reg={label} tool=Shell out=ok\n" for label in labels).encode())

    def test_one_silent_shell_selecting_registration_among_firing_ones_is_the_only_one_flagged(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        labels = self._selecting_labels(world)
        silent = labels[0]
        self._log_shell_calls(world, [label for label in labels if label != silent])

        assert sorted(set(self._flagged(world))) == [silent]

    def test_no_flag_when_only_the_literal_shell_selecting_registrations_fired(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        self._log_shell_calls(world, list(LITERAL_SHELL_SELECTING_LABELS))

        assert self._flagged(world) == []

    def test_no_flag_when_every_shell_selecting_registration_has_fired(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        self._log_shell_calls(world, self._selecting_labels(world))

        assert self._flagged(world) == []

    def test_no_flag_when_no_log_line_names_the_shell_tool(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        write_raw_log(world, b"reg=require-code-review tool=Write out=ok\n")

        assert self._flagged(world) == []

    def test_the_check_entry_and_the_pin_check_entry_are_never_named_when_every_selecting_registration_is_silent(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()
        labels = self._selecting_labels(world)
        write_raw_log(world, b"reg=check-hook-env tool=Shell out=ok\n")

        flagged = self._flagged(world)

        assert sorted(set(flagged)) == labels
        assert "check" not in flagged
        assert "pin-check" not in flagged


# --- install's own prerequisite check ---


def stow_without_hook_script(world: World, script: str) -> None:
    stowed_hooks = world.home / ".claude" / "hooks"
    stowed_hooks.unlink()
    stowed_hooks.mkdir()
    for hook_file in (CLAUDE_DIR / "hooks").iterdir():
        if hook_file.name != script:
            (stowed_hooks / hook_file.name).symlink_to(hook_file)


def stow_without_shim(world: World) -> None:
    stowed_scripts = world.home / ".claude" / "scripts"
    stowed_scripts.unlink()
    stowed_scripts.mkdir()
    for item in (CLAUDE_DIR / "scripts").iterdir():
        if item.name != "cursor-adapter":
            (stowed_scripts / item.name).symlink_to(item)
    stowed_adapter = stowed_scripts / "cursor-adapter"
    stowed_adapter.mkdir()
    for item in ADAPTER_DIR.iterdir():
        if item.name != "hook-shim.py":
            (stowed_adapter / item.name).symlink_to(item)


class TestInstallRefusesAnEntryThatCouldNotRun:
    @pytest.mark.parametrize("missing", ["hook-script", "shim"])
    def test_install_names_the_stowed_file_an_entry_would_run_and_writes_nothing_when_it_is_absent(
        self, fast_world: World, missing: str
    ) -> None:
        world = fast_world
        world.hooks_path.write_bytes(ORIGINAL_HOOKS)
        if missing == "hook-script":
            absent = world.adapter.stow_hook_path("require-code-review.sh")
            stow_without_hook_script(world, "require-code-review.sh")
        else:
            absent = world.adapter.stow_shim_path()
            stow_without_shim(world)
        assert not os.path.exists(absent)
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert f"{absent} is missing, so an entry the install would write could not run" in result.err
        assert world.snapshot() == before


# --- the sessionStart report's shape ---


class TestSessionStartReportShape:
    def test_a_clean_report_ends_on_no_findings_and_holds_no_finding_line(self, fast_world: World) -> None:
        world = fast_world
        world.install()

        context = run_surface(world, HOOK_ENV_SURFACE).out

        assert context.splitlines()[-1] == "no findings"
        assert "finding:" not in context

    def test_every_report_line_that_is_neither_informational_nor_a_note_or_finding_is_absent(self, fast_world: World) -> None:
        world = fast_world
        world.hooks_path.write_bytes(ORIGINAL_HOOKS)
        world.install()
        document = hooks_document(world)
        del document["hooks"]["preToolUse"][0]
        save_hooks(world, document)

        lines = run_surface(world, HOOK_ENV_SURFACE).out.splitlines()

        assert any(line.startswith("finding: missing or altered entry") for line in lines)
        assert "note: non-owned entries by event: stop 1" in lines
        assert "no findings" not in lines
        informational = [line for line in lines if not line.startswith(("finding: ", "note: "))]
        assert informational[0] == "Cursor adapter check:"
        assert all(
            line.startswith(("Cursor adapter check:", "Claude Code environment names", "Locale names", "A Cursor started"))
            or line == world.adapter.AGENT_RESTRAINT_SENTENCE
            or "run mode" in line
            or "acceptance-only" in line
            for line in informational
        ), informational

    def test_the_report_for_a_shim_that_does_not_load_leaves_the_checkout_to_the_engineer(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        load_adapter_copy_with(world, "hook-shim.py", "def broken(:\n")

        context = run_surface(world, HOOK_ENV_SURFACE).out

        line = next(line for line in context.splitlines() if "hook-shim.py did not load" in line)
        assert line.startswith("finding: ")
        assert line.endswith(world.adapter.AGENT_RESTORE_SENTENCE)
        assert "re-run `install`" not in line

    def test_the_restraint_line_precedes_the_first_finding_after_a_generation_failure(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        world.install()
        use_settings(world, monkeypatch, UNTRANSLATABLE_SETTINGS)

        lines = run_surface(world, HOOK_ENV_SURFACE).out.splitlines()

        first_finding = next(index for index, line in enumerate(lines) if line.startswith("finding: "))
        assert lines.count(world.adapter.AGENT_RESTRAINT_SENTENCE) == 1
        assert lines.index(world.adapter.AGENT_RESTRAINT_SENTENCE) < first_finding

    def test_the_restraint_line_precedes_the_first_finding_after_a_tamper(self, fast_world: World) -> None:
        world = fast_world
        world.install()
        document = hooks_document(world)
        del document["hooks"]["preToolUse"][0]
        save_hooks(world, document)

        lines = run_surface(world, HOOK_ENV_SURFACE).out.splitlines()

        first_finding = next(index for index, line in enumerate(lines) if line.startswith("finding: "))
        assert lines.count(world.adapter.AGENT_RESTRAINT_SENTENCE) == 1
        assert lines.index(world.adapter.AGENT_RESTRAINT_SENTENCE) < first_finding

    def test_the_restraint_line_is_absent_when_there_are_no_findings(self, fast_world: World) -> None:
        world = fast_world
        world.install()

        context = run_surface(world, HOOK_ENV_SURFACE).out

        assert world.adapter.AGENT_RESTRAINT_SENTENCE not in context


# --- copy: the command a terminal pastes ---


class TestInvocationText:
    def test_the_printed_command_survives_a_home_with_a_space_when_a_shell_reads_it_back(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spaced_home = world.tmp_path / "a home"
        monkeypatch.setenv("HOME", str(spaced_home))

        words = shlex.split(world.adapter.invocation("check"))

        assert words == ["python3", f"{spaced_home}/.claude/scripts/cursor-adapter/cursor-adapter.py", "check"]


# --- branches no other row reaches ---


class TestSmallBranches:
    def test_status_says_the_hooks_python3_is_unavailable_when_the_recorded_environment_cannot_be_built(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()
        shim_source = (ADAPTER_DIR / "hook-shim.py").read_text()
        assert "def hook_environment(" in shim_source
        load_adapter_copy_with(world, "hook-shim.py", shim_source.replace("def hook_environment(", "def renamed_environment(", 1))

        result = world.run("status")

        assert result.status == 0, result.err
        assert "hooks' python3: unavailable (AttributeError)" in result.out

    @pytest.mark.parametrize("element", ["a bare string", None, 7, ["nested"]], ids=repr)
    def test_an_event_array_element_that_is_no_object_is_a_non_owned_entry_and_printed_by_value(
        self, world: World, element: object
    ) -> None:
        assert world.adapter.ownership(element) == "non-owned"

        text = world.adapter.describe_entry("stop", 3, element)

        assert text == f"stop [3]: value={json.dumps(element)}"

    def test_install_check_and_uninstall_keep_array_elements_that_are_no_objects_and_report_them_by_count_and_value(
        self, fast_world: World
    ) -> None:
        world = fast_world
        original = json.dumps(
            {"version": 1, "hooks": {"stop": ["a bare string", None, {"command": "echo mine"}]}}, indent=2
        ).encode()
        world.hooks_path.write_bytes(original)
        assert world.install().status == 0
        document = hooks_document(world)
        document["hooks"]["stop"].append("added bare string")
        save_hooks(world, document)

        checked = world.run("check")
        uninstalled = world.run("uninstall")

        assert checked.status == 0, checked.out
        assert "note: non-owned entries by event: stop 4" in checked.out
        assert uninstalled.status == 0, uninstalled.err
        assert 'stop [3]: value="added bare string"' in uninstalled.out
        assert json.loads(world.hooks_path.read_text())["hooks"]["stop"] == [
            "a bare string",
            None,
            {"command": "echo mine"},
            "added bare string",
        ]


# --- a sessionStart entry whose payload stream never closes ---


def run_hook_env_with_stdin_held_open(world: World, seconds: int = 30) -> tuple[int, list[str], float]:
    """(exit status, stdout lines, seconds taken) of the real sessionStart command whose stdin never reaches EOF."""
    import time

    errors = world.tmp_path / "held-open-stderr"
    with errors.open("wb") as error_file:
        process = subprocess.Popen(
            [sys.executable, "-I", "-S", str(ADAPTER_SOURCE), "check", "--hook-env"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=error_file,
            env={"HOME": str(world.home), "PATH": str(world.bin_dir)},
        )
    outcome: dict[str, bytes] = {}
    started = time.monotonic()
    reader = threading.Thread(target=lambda: outcome.update(stdout=process.stdout.read()), daemon=True)
    reader.start()
    reader.join(seconds)
    try:
        assert not reader.is_alive(), "check --hook-env blocked on a payload stream that never closed"
        process.wait(timeout=seconds)
    finally:
        elapsed = time.monotonic() - started
        process.kill()
        process.wait()
        process.stdin.close()
        process.stdout.close()
    return process.returncode, outcome["stdout"].decode().splitlines(), elapsed


class TestHookEnvPayloadHeldOpen:
    def test_an_entry_whose_payload_stream_never_closes_still_answers_logs_itself_and_writes_no_first_call_mark(
        self, fast_world: World
    ) -> None:
        world = fast_world
        world.install()

        status, lines, _ = run_hook_env_with_stdin_held_open(world)

        assert status == 0
        assert len(lines) == 1
        assert json.loads(lines[0])["additional_context"].startswith("Cursor adapter check:")
        assert [fields["reg"] for fields in world.adapter.read_log(str(world.state_dir)).lines] == ["check-hook-env"]
        first_call_directory = world.state_dir / "first-call"
        assert not first_call_directory.exists() or not list(first_call_directory.iterdir())

    @pytest.mark.timing
    def test_an_entry_whose_payload_stream_never_closes_answers_inside_its_own_timeout(self, fast_world: World) -> None:
        world = fast_world
        world.install()

        _, _, elapsed = run_hook_env_with_stdin_held_open(world)

        assert elapsed < world.adapter.CHECK_ENTRY_TIMEOUT_SECONDS
