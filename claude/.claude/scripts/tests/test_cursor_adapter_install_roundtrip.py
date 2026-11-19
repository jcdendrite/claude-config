"""cursor-adapter.py install: its writes and their round trip, the tool-path rule, crash injection and the shim's record."""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest
from helpers import CLAUDE_DIR

from . import _cursor_install_helpers as install_helpers
from ._cursor_install_helpers import (
    ORIGINAL_HOOKS,
    Crash,
    World,
    crash_at,
    hooks_document,
    make_tool_bin,
    save_hooks,
    stop_crashing,
)

sys.path.insert(0, str(CLAUDE_DIR))
from hooks.tests._cursor_payloads import (  # noqa: E402 (the import needs the sys.path entry above)
    ADAPTER_DIR,
    load_json,
    wrapper_name,
    write_sh_script,
)

# The fixtures live once in the helper module. An import would trip F811 on every test parameter that names one.
world = install_helpers.world
fast_world = install_helpers.fast_world


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

    @pytest.mark.parametrize("uninstall_branch", ["pre-image-restore", "rewrite"])
    def test_an_existing_hooks_file_keeps_its_mode_through_install_reinstall_and_uninstall(
        self, fast_world: World, uninstall_branch: str
    ) -> None:
        world = fast_world
        original = b'{"version": 1, "hooks": {"stop": [{"command": "echo mine"}]}}\n'
        world.hooks_path.write_bytes(original)
        world.hooks_path.chmod(0o644)

        world.install()
        assert stat.S_IMODE(world.hooks_path.stat().st_mode) == 0o644
        world.install()
        assert stat.S_IMODE(world.hooks_path.stat().st_mode) == 0o644
        if uninstall_branch == "rewrite":
            document = json.loads(world.hooks_path.read_text())
            document["hooks"]["stop"].append({"command": "echo added later"})
            world.hooks_path.write_text(json.dumps(document))
        world.run("uninstall")

        if uninstall_branch == "rewrite":
            assert b"echo added later" in world.hooks_path.read_bytes()
        else:
            assert world.hooks_path.read_bytes() == original
        assert stat.S_IMODE(world.hooks_path.stat().st_mode) == 0o644

    def test_the_install_record_the_pre_install_file_and_the_manifest_are_written_owner_only(self, fast_world: World) -> None:
        world = fast_world
        world.hooks_path.write_bytes(b'{"version": 1, "hooks": {}}\n')
        world.hooks_path.chmod(0o644)

        world.install()

        for name in ("install.json", "hooks.json.pre-install", "manifest.json"):
            assert stat.S_IMODE((world.record_dir / name).stat().st_mode) == 0o600, name

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

    @pytest.mark.parametrize(
        ("member", "value"),
        [("added", 1), ("extra", [3]), ("version", 2)],
        ids=["member-added", "member-changed", "version-changed"],
    )
    def test_a_top_level_member_changed_after_install_survives_an_uninstall_that_removes_only_the_owned_entries(
        self, fast_world: World, member: str, value: object
    ) -> None:
        world = fast_world
        world.hooks_path.write_bytes(ORIGINAL_HOOKS)
        world.install()
        document = hooks_document(world)
        document[member] = value
        save_hooks(world, document)

        result = world.run("uninstall")

        assert result.status == 0, result.err
        uninstalled = hooks_document(world)
        assert uninstalled[member] == value
        assert uninstalled["hooks"] == {"stop": [{"command": "echo mine"}]}
        assert not any(world.adapter.ownership(item) == "owned" for items in uninstalled["hooks"].values() for item in items)

    @pytest.mark.parametrize(("member", "value"), [("added", 1), ("version", 2)], ids=["member-added", "version-changed"])
    def test_a_top_level_member_changed_after_a_no_file_install_keeps_the_file_through_uninstall(
        self, fast_world: World, member: str, value: object
    ) -> None:
        world = fast_world
        assert not world.hooks_path.exists()
        world.install()
        assert (world.record_dir / "hooks.json.pre-install.none").exists()
        document = hooks_document(world)
        document[member] = value
        save_hooks(world, document)

        result = world.run("uninstall")

        assert result.status == 0, result.err
        uninstalled = hooks_document(world)
        assert uninstalled[member] == value
        assert uninstalled["hooks"] == {}

    def test_an_original_hooks_file_with_an_empty_event_array_is_restored_byte_identical_by_uninstall(
        self, fast_world: World
    ) -> None:
        world = fast_world
        original = b'{"version": 1, "hooks": {"stop": []}}\n'
        world.hooks_path.write_bytes(original)
        world.install()

        result = world.run("uninstall")

        assert result.status == 0, result.err
        assert world.hooks_path.read_bytes() == original

    def test_an_original_hooks_file_with_no_hooks_member_is_restored_byte_identical_by_uninstall(self, fast_world: World) -> None:
        world = fast_world
        original = b'{"version": 1}\n'
        world.hooks_path.write_bytes(original)
        installed = world.install()
        assert installed.status == 0, installed.err
        assert (world.record_dir / "hooks.json.pre-install").read_bytes() == original

        result = world.run("uninstall")

        assert result.status == 0, result.err
        assert world.hooks_path.read_bytes() == original

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


# --- crash injection ---


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

    def test_an_interrupted_uninstall_then_a_deleted_hooks_json_then_install_swaps_the_stale_pre_install_file_for_the_none_marker(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        world.hooks_path.write_bytes(ORIGINAL_HOOKS)
        world.install()
        crash_at(world, monkeypatch, "after-entries")
        with pytest.raises(Crash):
            world.run("uninstall")
        stop_crashing(world, monkeypatch)
        assert (world.record_dir / "hooks.json.pre-install").exists()
        world.hooks_path.unlink()

        assert world.install().status == 0

        assert not (world.record_dir / "hooks.json.pre-install").exists()
        assert (world.record_dir / "hooks.json.pre-install.none").exists()
        assert world.run("uninstall").status == 0
        assert not world.hooks_path.exists()
        assert not world.record_dir.exists()

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
