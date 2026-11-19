"""cursor-adapter.py plain check and what its reports print, the sessionStart report's shape included."""

from __future__ import annotations

import json
import os
import shlex
import sys

import pytest
from helpers import CLAUDE_DIR

from . import _cursor_install_helpers as install_helpers
from ._cursor_install_helpers import (
    HOOK_ENV_SURFACE,
    ORIGINAL_HOOKS,
    SURFACES,
    World,
    hooks_document,
    load_adapter_copy_with,
    run_surface,
    save_hooks,
    use_settings,
)

sys.path.insert(0, str(CLAUDE_DIR))
from hooks.tests._cursor_payloads import (  # noqa: E402 (the import needs the sys.path entry above)
    ADAPTER_DIR,
    wrapper_name,
)

# The fixtures live once in the helper module. An import would trip F811 on every test parameter that names one.
world = install_helpers.world
fast_world = install_helpers.fast_world


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
        assert hooks_document(world)["added"] == 1
        assert world.install().status == 0
        assert hooks_document(world)["added"] == 1
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
            line.startswith(("Cursor adapter check:", "Locale names"))
            or line == world.adapter.AGENT_RESTRAINT_SENTENCE
            or "run mode" in line
            or "acceptance-only" in line
            or line.startswith("Host walk: ")
            or line in (world.adapter.HOST_WALK_NOT_FOUND_LINE, world.adapter.HOST_WALK_CLAUDE_FIRST_LINE)
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
