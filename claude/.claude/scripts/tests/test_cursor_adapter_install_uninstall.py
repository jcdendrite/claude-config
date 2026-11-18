"""cursor-adapter.py ownership, the print set and uninstall, an unusable pre-install file included."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from . import _cursor_install_helpers as install_helpers
from ._cursor_install_helpers import (
    ORIGINAL_HOOKS,
    SURFACES,
    Result,
    World,
    hooks_document,
    load_adapter_copy_with,
    run_surface,
    save_hooks,
)

# The fixtures live once in the helper module. An import would trip F811 on every test parameter that names one.
world = install_helpers.world
fast_world = install_helpers.fast_world


# --- ownership, the print set and uninstall ---


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
            f"{{ out=$({python_path} -I -S {adapter_path} check --hook-env); }} 2>/dev/null; rc=$?; "
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
