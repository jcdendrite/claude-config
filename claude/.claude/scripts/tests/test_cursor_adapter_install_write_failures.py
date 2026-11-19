"""cursor-adapter.py write failures: a failure after the first write, the write targets, fsync and an uncaught exception."""

from __future__ import annotations

import errno
import json
import os
import re
import shutil
import stat
import sys
import types
from pathlib import Path

import pytest
from helpers import CLAUDE_DIR

from . import _cursor_install_helpers as install_helpers
from ._cursor_install_helpers import (
    ORIGINAL_HOOKS,
    Result,
    World,
    hooks_document,
    load_adapter_copy_with,
    use_settings,
)

sys.path.insert(0, str(CLAUDE_DIR))
from hooks.tests._cursor_payloads import (  # noqa: E402 (the import needs the sys.path entry above)
    write_sh_script,
)

# The fixtures live once in the helper module. An import would trip F811 on every test parameter that names one.
world = install_helpers.world
fast_world = install_helpers.fast_world


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
        result = fast_world.run("install", "--config-dir", "relative/config")

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

    def test_a_durable_write_that_fails_at_the_rename_keeps_the_old_bytes_and_leaves_no_temp_file(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = world.tmp_path / "durable.txt"
        target.write_bytes(b"old bytes")

        def refuse_rename(source, destination):
            raise OSError(errno.EIO, "rename refused")

        # The adapter's `os` is the process-wide module, so monkeypatch restores it at teardown.
        monkeypatch.setattr(world.adapter.os, "replace", refuse_rename)

        with pytest.raises(OSError, match="rename refused"):
            world.adapter.durable_write(str(target), b"new bytes")

        assert target.read_bytes() == b"old bytes"
        assert [path.name for path in world.tmp_path.iterdir() if path.name.startswith(world.adapter.TEMP_FILE_PREFIX)] == []
