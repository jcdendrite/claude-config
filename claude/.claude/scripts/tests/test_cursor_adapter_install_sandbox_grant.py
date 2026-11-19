"""cursor-adapter.py's user-level sandbox.json grant, and a grant left pending."""

from __future__ import annotations

import errno
import json
import os
import sys
from pathlib import Path

import pytest
from helpers import CLAUDE_DIR

from . import _cursor_install_helpers as install_helpers
from ._cursor_install_helpers import (
    ORIGINAL_HOOKS,
    Crash,
    Result,
    World,
    crash_at,
    stop_crashing,
)

sys.path.insert(0, str(CLAUDE_DIR))
from hooks.tests._cursor_payloads import (  # noqa: E402 (the import needs the sys.path entry above)
    ADAPTER_DIR,
    IS_ROOT,
    load_json,
)

# The fixtures live once in the helper module. An import would trip F811 on every test parameter that names one.
world = install_helpers.world
fast_world = install_helpers.fast_world


# --- the user-level sandbox.json grant ---

ENGINEER_SANDBOX = b'{"additionalReadwritePaths": ["/mine"], "extra": {"keep": [1, 2]}}\n'
SANDBOX_PATHS_KEY = "additionalReadwritePaths"
GRANT_INSTALL_POINTS = [
    "after-pre-image",
    "after-sandbox-pre-image",
    "after-sandbox-records",
    "after-install-record",
    "after-generated-files",
    "after-hooks-json",
    "after-sandbox-dirs",
    "after-sandbox-file",
    "after-manifest",
]
GRANT_UNINSTALL_POINTS = ["after-sandbox", "after-entries", "after-generated-files", "after-state", "after-record"]


def grant_parity(world: World, monkeypatch: pytest.MonkeyPatch, *, pending: bool) -> None:
    """Points the adapter at a parity file of the test's own, so a flip of the repository's file never changes a row."""
    document = load_json(ADAPTER_DIR / "parity-exceptions.json")
    gate = ("adapter-gate", "gate-settings-permissions.sh")
    document["entries"] = [entry for entry in document["entries"] if (entry["kind"], entry["key"]) != gate]
    if pending:
        document["entries"].append({"kind": gate[0], "key": gate[1], "reason": "pending-phase", "phase": "5a", "detail": "x"})
    path = world.tmp_path / "grant-parity.json"
    path.write_text(json.dumps(document))
    monkeypatch.setattr(world.adapter, "grant_exceptions_path", lambda: str(path))


def sandbox_file(world: World) -> Path:
    return world.cursor_dir / "sandbox.json"


def live_sandbox_bytes(world: World) -> bytes | None:
    return sandbox_file(world).read_bytes() if sandbox_file(world).exists() else None


def listed_directories(world: World) -> list[str]:
    return [str(world.config_dir / name) for name in world.adapter.SANDBOX_WRITABLE_DIRECTORIES]


class TestSandboxFile:
    @pytest.fixture
    def grant_world(self, fast_world: World, monkeypatch: pytest.MonkeyPatch) -> World:
        grant_parity(fast_world, monkeypatch, pending=False)
        return fast_world

    @pytest.mark.parametrize("original", [None, ENGINEER_SANDBOX], ids=["absent", "engineers-own"])
    def test_install_writes_the_listed_paths_and_uninstall_leaves_the_original_bytes_or_no_file(
        self, grant_world: World, original: bytes | None
    ) -> None:
        world = grant_world
        if original is not None:
            sandbox_file(world).write_bytes(original)

        assert world.install().status == 0
        installed = json.loads(live_sandbox_bytes(world))
        assert set(listed_directories(world)) <= set(installed[SANDBOX_PATHS_KEY])
        if original is not None:
            assert installed["extra"] == {"keep": [1, 2]}
            assert "/mine" in installed[SANDBOX_PATHS_KEY]
        assert all((world.config_dir / name).is_dir() for name in world.adapter.SANDBOX_WRITABLE_DIRECTORIES)

        assert world.run("uninstall").status == 0
        assert live_sandbox_bytes(world) == original
        assert not world.record_dir.exists()

    @pytest.mark.parametrize("original", [None, ENGINEER_SANDBOX], ids=["absent", "engineers-own"])
    @pytest.mark.parametrize("point", GRANT_INSTALL_POINTS)
    def test_a_crash_at_each_install_point_then_uninstall_leaves_the_same(
        self, grant_world: World, monkeypatch: pytest.MonkeyPatch, point: str, original: bytes | None
    ) -> None:
        world = grant_world
        if original is not None:
            sandbox_file(world).write_bytes(original)
        crash_at(world, monkeypatch, point)
        with pytest.raises(Crash):
            world.install()
        stop_crashing(world, monkeypatch)

        assert world.run("uninstall").status == 0
        assert live_sandbox_bytes(world) == original
        assert not world.record_dir.exists()

    @pytest.mark.parametrize("original", [None, ENGINEER_SANDBOX], ids=["absent", "engineers-own"])
    @pytest.mark.parametrize("point", GRANT_UNINSTALL_POINTS)
    def test_a_crash_at_each_uninstall_point_then_install_and_uninstall_leaves_the_same(
        self, grant_world: World, monkeypatch: pytest.MonkeyPatch, point: str, original: bytes | None
    ) -> None:
        world = grant_world
        if original is not None:
            sandbox_file(world).write_bytes(original)
        world.install()
        crash_at(world, monkeypatch, point)
        with pytest.raises(Crash):
            world.run("uninstall")
        stop_crashing(world, monkeypatch)

        assert world.install().status == 0
        assert world.run("uninstall").status == 0
        assert live_sandbox_bytes(world) == original

    @pytest.mark.parametrize("original", [None, ENGINEER_SANDBOX], ids=["absent", "engineers-own"])
    def test_a_kill_after_the_sandbox_rename_and_before_the_manifest_then_install_keeps_the_first_pre_image(
        self, grant_world: World, monkeypatch: pytest.MonkeyPatch, original: bytes | None
    ) -> None:
        world = grant_world
        if original is not None:
            sandbox_file(world).write_bytes(original)
        crash_at(world, monkeypatch, "after-sandbox-file")
        with pytest.raises(Crash):
            world.install()
        stop_crashing(world, monkeypatch)
        pre_image = {
            name: (world.record_dir / name).read_bytes()
            for name in ("sandbox.json.pre-install", "sandbox.json.pre-install.none")
            if (world.record_dir / name).exists()
        }

        assert world.install().status == 0
        assert pre_image == {
            name: (world.record_dir / name).read_bytes() for name in pre_image if (world.record_dir / name).exists()
        }
        assert world.run("uninstall").status == 0
        assert live_sandbox_bytes(world) == original

    def test_an_install_with_the_grant_pending_writes_neither_file_nor_record_and_a_later_grant_keeps_the_engineers_edit(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        grant_parity(world, monkeypatch, pending=True)
        sandbox_file(world).write_bytes(ENGINEER_SANDBOX)

        assert world.install().status == 0

        assert live_sandbox_bytes(world) == ENGINEER_SANDBOX
        assert sorted(path.name for path in world.record_dir.iterdir() if "sandbox" in path.name) == []
        assert not any((world.config_dir / name).exists() for name in world.adapter.SANDBOX_WRITABLE_DIRECTORIES)
        edited = b'{"additionalReadwritePaths": ["/mine", "/edited"], "extra": {"keep": [1, 2]}}\n'
        sandbox_file(world).write_bytes(edited)
        grant_parity(world, monkeypatch, pending=False)
        assert world.install().status == 0
        assert world.run("uninstall").status == 0
        assert live_sandbox_bytes(world) == edited

    def test_status_lists_the_grant_pending_while_its_prerequisite_is_listed_and_not_after(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        grant_parity(world, monkeypatch, pending=True)
        world.install()
        assert world.adapter.SANDBOX_GRANT_PENDING_LINE in world.run("status").out
        grant_parity(world, monkeypatch, pending=False)
        world.install()
        assert world.adapter.SANDBOX_GRANT_PENDING_LINE not in world.run("status").out

    def test_an_engineers_edit_after_install_survives_uninstall_with_only_the_recorded_paths_removed(
        self, grant_world: World
    ) -> None:
        world = grant_world
        sandbox_file(world).write_bytes(ENGINEER_SANDBOX)
        world.install()
        document = json.loads(live_sandbox_bytes(world))
        document[SANDBOX_PATHS_KEY].append("/later")
        document["added-key"] = True
        sandbox_file(world).write_text(json.dumps(document))

        assert world.run("uninstall").status == 0

        assert json.loads(live_sandbox_bytes(world)) == {
            SANDBOX_PATHS_KEY: ["/mine", "/later"],
            "extra": {"keep": [1, 2]},
            "added-key": True,
        }

    @pytest.mark.parametrize(
        "content",
        [b"{not json", b"[]", b'{"additionalReadwritePaths": "x"}', b'{"additionalReadwritePaths": ["a", 3]}'],
        ids=["unparseable", "non-object", "non-list-paths", "non-string-path"],
    )
    def test_a_file_that_does_not_parse_or_has_the_wrong_shape_makes_install_refuse_naming_it_with_no_write(
        self, grant_world: World, content: bytes
    ) -> None:
        world = grant_world
        sandbox_file(world).write_bytes(content)
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert str(sandbox_file(world)) in result.err
        assert world.snapshot() == before

    @pytest.mark.parametrize("how_many", ["one", "all"])
    def test_an_engineers_file_that_already_lists_some_or_all_of_the_paths_keeps_them_through_install_and_uninstall(
        self, grant_world: World, how_many: str
    ) -> None:
        world = grant_world
        listed = listed_directories(world)
        own = listed[:1] if how_many == "one" else listed
        original = json.dumps({SANDBOX_PATHS_KEY: own}).encode()
        sandbox_file(world).write_bytes(original)

        assert world.install().status == 0
        if how_many == "all":
            assert live_sandbox_bytes(world) == original
        assert world.run("uninstall").status == 0

        assert live_sandbox_bytes(world) == original

    def test_install_names_the_directories_the_engineer_already_listed_and_no_others(self, grant_world: World) -> None:
        world = grant_world
        already_listed_line = "of the sandbox grant's directories are already listed in"
        sandbox_file(world).write_bytes(json.dumps({SANDBOX_PATHS_KEY: listed_directories(world)[:1]}).encode())

        over_engineers_file = world.install()
        reinstalled_over_engineers_file = world.install()

        assert f"1 {already_listed_line}" in over_engineers_file.out
        assert f"1 {already_listed_line}" in reinstalled_over_engineers_file.out
        assert world.run("uninstall").status == 0
        sandbox_file(world).unlink()

        over_absent_file = world.install()
        reinstalled_over_absent_file = world.install()

        assert already_listed_line not in over_absent_file.out
        assert already_listed_line not in reinstalled_over_absent_file.out

    def test_install_twice_then_uninstall_leaves_the_original_bytes(self, grant_world: World) -> None:
        world = grant_world
        sandbox_file(world).write_bytes(ENGINEER_SANDBOX)
        world.install()
        once = live_sandbox_bytes(world)

        assert world.install().status == 0
        assert live_sandbox_bytes(world) == once
        assert world.run("uninstall").status == 0
        assert live_sandbox_bytes(world) == ENGINEER_SANDBOX

    def test_a_later_list_and_a_build_that_writes_no_grant_leave_nothing_of_an_earlier_list_after_uninstall(
        self, grant_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = grant_world
        sandbox_file(world).write_bytes(ENGINEER_SANDBOX)
        first_list = world.adapter.SANDBOX_WRITABLE_DIRECTORIES
        assert world.install().status == 0
        monkeypatch.setattr(world.adapter, "SANDBOX_WRITABLE_DIRECTORIES", (*first_list[1:], "a-new-directory"))
        assert world.install().status == 0
        after_second_list = json.loads(live_sandbox_bytes(world))[SANDBOX_PATHS_KEY]
        assert {str(world.config_dir / "a-new-directory"), str(world.config_dir / first_list[0])} <= set(after_second_list)
        grant_parity(world, monkeypatch, pending=True)
        assert world.install().status == 0

        assert world.run("uninstall").status == 0

        assert live_sandbox_bytes(world) == ENGINEER_SANDBOX

    def test_a_populated_listed_directory_survives_uninstall_and_an_absent_one_is_created_by_install(
        self, grant_world: World
    ) -> None:
        world = grant_world
        populated = world.config_dir / world.adapter.SANDBOX_WRITABLE_DIRECTORIES[0]
        populated.mkdir()
        (populated / "marker").write_text("kept")
        absent = world.config_dir / world.adapter.SANDBOX_WRITABLE_DIRECTORIES[1]
        assert not absent.exists()

        world.install()
        assert absent.is_dir()
        world.run("uninstall")

        assert (populated / "marker").read_text() == "kept"
        assert absent.is_dir()

    def test_a_listed_directory_that_is_a_symlink_is_refused_with_no_write(self, grant_world: World) -> None:
        world = grant_world
        target = world.tmp_path / "elsewhere"
        target.mkdir()
        (world.config_dir / world.adapter.SANDBOX_WRITABLE_DIRECTORIES[0]).symlink_to(target)
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert "symlink" in result.err
        assert world.snapshot() == before

    def test_the_comparison_names_removed_paths_and_changed_keys_by_count_shows_later_paths_as_a_count_and_echoes_nothing(
        self, grant_world: World
    ) -> None:
        world = grant_world
        sandbox_file(world).write_bytes(ENGINEER_SANDBOX)
        world.install()
        for surface in ("status", "check"):
            assert "sandbox.json" not in "\n".join(
                line for line in world.run(surface).out.splitlines() if line.startswith(("finding:", "note:"))
            )
        document = json.loads(live_sandbox_bytes(world))
        document[SANDBOX_PATHS_KEY] = [item for item in document[SANDBOX_PATHS_KEY] if item != "/mine"]
        document[SANDBOX_PATHS_KEY].append("/NONCE-path")
        document["NONCE-key"] = 1
        sandbox_file(world).write_text(json.dumps(document))

        for surface in ("status", "check"):
            out = world.run(surface).out
            assert "no longer lists 1 path(s)" in out
            assert "has 1 key(s) that differ" in out
            assert "1 path(s) in sandbox.json were added since install" in out
            assert "NONCE" not in out

    def test_an_absent_paths_key_in_the_engineers_file_is_restored_to_the_engineers_own_bytes(self, grant_world: World) -> None:
        world = grant_world
        original = b'{"networkPolicy": {"allow": ["example.invalid"]}}\n'
        sandbox_file(world).write_bytes(original)
        world.install()
        assert SANDBOX_PATHS_KEY in json.loads(live_sandbox_bytes(world))

        assert world.run("uninstall").status == 0

        assert live_sandbox_bytes(world) == original

    def _assert_only_the_grant_was_blocked(self, world: World, result: Result, live: bytes) -> None:
        """The sandbox file is as it was, its record and pre-image stay, and the hooks.json step still ran."""
        assert result.status == 0, result.err
        assert live_sandbox_bytes(world) == live
        assert "the sandbox grant in sandbox.json remains" in result.out
        assert not world.hooks_path.exists()
        assert (world.record_dir / "manifest.json").exists()

    def test_a_pre_image_that_cannot_be_read_blocks_only_the_grant_and_uninstall_still_removes_the_hooks_entries(
        self, grant_world: World
    ) -> None:
        world = grant_world
        sandbox_file(world).write_bytes(ENGINEER_SANDBOX)
        world.install()
        installed = live_sandbox_bytes(world)
        (world.record_dir / "sandbox.json.pre-install").unlink()
        (world.record_dir / "sandbox.json.pre-install").mkdir()

        result = world.run("uninstall")

        self._assert_only_the_grant_was_blocked(world, result, installed)

    def test_a_document_json_cannot_write_back_blocks_only_the_grant_and_uninstall_still_removes_the_hooks_entries(
        self, grant_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = grant_world
        sandbox_file(world).write_bytes(ENGINEER_SANDBOX)
        world.install()
        with_nan = json.dumps(json.loads(live_sandbox_bytes(world)))[:-1].encode() + b', "member": NaN}'
        sandbox_file(world).write_bytes(with_nan)

        def shim_that_does_not_load(path=None):
            raise RuntimeError("shim unavailable")

        monkeypatch.setattr(world.adapter, "load_shim", shim_that_does_not_load)

        result = world.run("uninstall")

        self._assert_only_the_grant_was_blocked(world, result, with_nan)
        assert "cannot write back" in result.out

    def test_a_failed_sandbox_write_blocks_only_the_grant_and_uninstall_still_removes_the_hooks_entries(
        self, grant_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = grant_world
        sandbox_file(world).write_bytes(ENGINEER_SANDBOX)
        world.install()
        installed = live_sandbox_bytes(world)
        real_write = world.adapter.durable_write

        def sandbox_write_that_fails(path, data, mode=0o600):
            if os.path.basename(path) == "sandbox.json":
                raise OSError(errno.EROFS, "read-only file system", path)
            real_write(path, data, mode)

        monkeypatch.setattr(world.adapter, "durable_write", sandbox_write_that_fails)

        result = world.run("uninstall")

        self._assert_only_the_grant_was_blocked(world, result, installed)
        assert "could not be changed" in result.out
        monkeypatch.setattr(world.adapter, "durable_write", real_write)
        assert world.run("uninstall").status == 0
        assert live_sandbox_bytes(world) == ENGINEER_SANDBOX

    def test_a_failed_sandbox_delete_blocks_only_the_grant_and_uninstall_still_removes_the_hooks_entries(
        self, grant_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = grant_world
        world.install()
        installed = live_sandbox_bytes(world)
        real_unlink = os.unlink

        def sandbox_unlink_that_fails(path, *arguments, **keywords):
            if os.path.basename(path) == "sandbox.json":
                raise OSError(errno.EPERM, "operation not permitted", path)
            return real_unlink(path, *arguments, **keywords)

        monkeypatch.setattr(world.adapter.os, "unlink", sandbox_unlink_that_fails)

        result = world.run("uninstall")

        self._assert_only_the_grant_was_blocked(world, result, installed)
        assert "could not be changed" in result.out

    def test_a_blocked_sandbox_step_finishes_on_a_later_uninstall_once_the_file_is_fixed(self, grant_world: World) -> None:
        world = grant_world
        sandbox_file(world).write_bytes(ENGINEER_SANDBOX)
        world.install()
        installed = live_sandbox_bytes(world)
        sandbox_file(world).write_bytes(b"{not json")

        blocked = world.run("uninstall")

        self._assert_only_the_grant_was_blocked(world, blocked, b"{not json")
        assert str(sandbox_file(world)) in blocked.out
        assert "`uninstall` did not remove the sandbox grant" in blocked.out
        assert "was not written" not in blocked.out
        assert (world.record_dir / "sandbox.json.pre-install").exists()
        sandbox_file(world).write_bytes(installed)

        assert world.run("uninstall").status == 0

        assert live_sandbox_bytes(world) == ENGINEER_SANDBOX
        assert not world.record_dir.exists()

    def test_a_hooks_json_edit_after_the_sandbox_step_tells_the_engineer_the_failure_may_name_no_file(
        self, grant_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = grant_world
        world.install()
        real_apply = world.adapter.apply_sandbox_action

        def apply_then_a_hooks_json_edit(*arguments):
            outcome = real_apply(*arguments)
            world.hooks_path.write_text(json.dumps({"version": 1, "hooks": {"stop": [{"command": "edited"}]}}))
            return outcome

        monkeypatch.setattr(world.adapter, "apply_sandbox_action", apply_then_a_hooks_json_edit)

        result = world.run("uninstall")

        assert result.status == world.adapter.PARTIAL_WRITE_EXIT_STATUS
        assert "after the step after-sandbox" in result.err
        assert "when no file is named, a change to hooks.json while `uninstall` ran" in result.err

    def test_an_edit_between_the_uninstall_plan_and_its_write_is_kept_and_the_record_stays(
        self, grant_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = grant_world
        sandbox_file(world).write_bytes(ENGINEER_SANDBOX)
        world.install()
        real_entries = world.adapter.entries_for_uninstall

        def entries_then_an_engineer_edit(*arguments):
            result = real_entries(*arguments)
            document = json.loads(live_sandbox_bytes(world))
            document[SANDBOX_PATHS_KEY].append("/edited-meanwhile")
            sandbox_file(world).write_text(json.dumps(document))
            return result

        monkeypatch.setattr(world.adapter, "entries_for_uninstall", entries_then_an_engineer_edit)

        result = world.run("uninstall")

        assert result.status == 0, result.err
        assert "changed while `uninstall` ran" in result.out
        kept_paths = json.loads(live_sandbox_bytes(world))[SANDBOX_PATHS_KEY]
        assert "/edited-meanwhile" in kept_paths
        assert set(listed_directories(world)) <= set(kept_paths)
        assert (world.record_dir / "sandbox.json.pre-install").exists()
        monkeypatch.setattr(world.adapter, "entries_for_uninstall", real_entries)

        assert world.run("uninstall").status == 0

        assert json.loads(live_sandbox_bytes(world)) == {
            SANDBOX_PATHS_KEY: ["/mine", "/edited-meanwhile"],
            "extra": {"keep": [1, 2]},
        }

    def test_a_failure_after_the_sandbox_step_does_not_call_hooks_json_handled(
        self, grant_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = grant_world
        sandbox_file(world).write_bytes(ENGINEER_SANDBOX)
        world.install()
        real_write = world.adapter.durable_write

        def hooks_write_that_fails(path, data, mode=0o600):
            if os.path.basename(path) == "hooks.json":
                raise OSError(errno.EROFS, "read-only file system", path)
            real_write(path, data, mode)

        world.hooks_path.write_bytes(ORIGINAL_HOOKS)
        world.install()
        monkeypatch.setattr(world.adapter, "durable_write", hooks_write_that_fails)

        result = world.run("uninstall")

        assert result.status == world.adapter.PARTIAL_WRITE_EXIT_STATUS
        assert "after-sandbox" in result.err
        assert "hooks.json is already handled" not in result.err
        assert "and hooks.json is not" in result.err
        assert "restore hooks.json by hand" in result.err

    def test_a_sandbox_json_that_is_a_symlink_makes_install_refuse_naming_it_with_no_write(self, grant_world: World) -> None:
        world = grant_world
        real_file = world.tmp_path / "real-sandbox.json"
        real_file.write_bytes(ENGINEER_SANDBOX)
        sandbox_file(world).symlink_to(real_file)
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert str(sandbox_file(world)) in result.err
        assert "symlink" in result.err
        assert world.snapshot() == before
        assert real_file.read_bytes() == ENGINEER_SANDBOX

    def test_a_sandbox_json_owned_by_someone_else_makes_install_refuse_naming_it_with_no_write(
        self, grant_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = grant_world
        sandbox_file(world).write_bytes(ENGINEER_SANDBOX)
        before = world.snapshot()
        real_plan = world.adapter.plan_sandbox_grant
        other_uid = os.getuid() + 1

        def plan_as_another_user(*arguments):
            with monkeypatch.context() as patched:
                patched.setattr(world.adapter.os, "getuid", lambda: other_uid)
                return real_plan(*arguments)

        monkeypatch.setattr(world.adapter, "plan_sandbox_grant", plan_as_another_user)

        result = world.install()

        assert result.status == 1
        assert str(sandbox_file(world)) in result.err
        assert "owned by you" in result.err
        assert world.snapshot() == before

    @pytest.mark.parametrize("mode", [0o664, 0o666, 0o646])
    def test_a_sandbox_json_writable_by_group_or_others_makes_install_refuse_naming_it_with_no_write(
        self, grant_world: World, mode: int
    ) -> None:
        world = grant_world
        sandbox_file(world).write_bytes(ENGINEER_SANDBOX)
        sandbox_file(world).chmod(mode)
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert str(sandbox_file(world)) in result.err
        assert "not writable by group or others" in result.err
        assert world.snapshot() == before

    def test_a_sandbox_json_that_group_and_others_cannot_write_is_accepted(self, grant_world: World) -> None:
        world = grant_world
        sandbox_file(world).write_bytes(ENGINEER_SANDBOX)
        sandbox_file(world).chmod(0o644)

        assert world.install().status == 0

    def test_a_listed_path_that_exists_as_a_regular_file_makes_install_refuse_naming_it_with_no_write(
        self, grant_world: World
    ) -> None:
        world = grant_world
        blocking_file = world.config_dir / world.adapter.SANDBOX_WRITABLE_DIRECTORIES[0]
        blocking_file.write_text("not a directory")
        before = world.snapshot()

        result = world.install()

        assert result.status == 1
        assert str(blocking_file) in result.err
        assert "is not a directory" in result.err
        assert world.snapshot() == before

    def test_a_sandbox_json_that_changes_between_the_plan_and_the_rename_is_left_alone_with_the_partial_write_status(
        self, grant_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = grant_world
        changed = b'{"additionalReadwritePaths": ["/changed-meanwhile"]}\n'
        real_generated_files = world.adapter.write_generated_files

        def generated_files_then_an_engineer_edit():
            result = real_generated_files()
            sandbox_file(world).write_bytes(changed)
            return result

        monkeypatch.setattr(world.adapter, "write_generated_files", generated_files_then_an_engineer_edit)

        result = world.install()

        assert result.status == world.adapter.PARTIAL_WRITE_EXIT_STATUS
        assert "changed while `install` ran" in result.err
        assert live_sandbox_bytes(world) == changed
        monkeypatch.setattr(world.adapter, "write_generated_files", real_generated_files)
        assert world.run("uninstall").status == 0
        assert live_sandbox_bytes(world) == changed

    def test_check_and_status_report_a_deleted_or_a_corrupted_sandbox_json_without_echoing_its_text(
        self, grant_world: World
    ) -> None:
        world = grant_world
        world.install()
        sandbox_file(world).unlink()
        for surface in ("status", "check"):
            assert "sandbox.json is missing, so the sandbox grant is gone" in world.run(surface).out

        sandbox_file(world).write_text("NONCE-not-json")

        for surface in ("status", "check"):
            out = world.run(surface).out
            assert "sandbox.json does not parse or has the wrong shape" in out
            assert "NONCE" not in out

    @pytest.mark.parametrize("surface", ["status", "check"])
    def test_a_recorded_grant_whose_prerequisite_is_pending_again_is_reported_by_count_and_not_called_unwritten(
        self, grant_world: World, monkeypatch: pytest.MonkeyPatch, surface: str
    ) -> None:
        world = grant_world
        world.install()
        grant_parity(world, monkeypatch, pending=True)
        listed = len(world.adapter.SANDBOX_WRITABLE_DIRECTORIES)

        out = world.run(surface).out

        assert world.adapter.SANDBOX_GRANT_PENDING_LINE not in out
        assert f"{listed} path(s) are recorded as the sandbox grant, and {listed} of them are listed in sandbox.json now" in out
        assert any(line.startswith("finding:") and "recorded as the sandbox grant" in line for line in out.splitlines())

    @pytest.mark.parametrize("surface", ["status", "check"])
    def test_a_recorded_grant_whose_prerequisite_is_pending_again_says_a_marker_can_be_written_only_while_a_path_is_listed(
        self, grant_world: World, monkeypatch: pytest.MonkeyPatch, surface: str
    ) -> None:
        world = grant_world
        world.install()
        grant_parity(world, monkeypatch, pending=True)
        marker_write_clause = world.adapter.SANDBOX_GRANT_MARKER_WRITE_CLAUSE

        listed = world.run(surface).out
        sandbox_file(world).write_text(json.dumps({SANDBOX_PATHS_KEY: ["/mine"]}))
        none_listed = world.run(surface).out
        sandbox_file(world).write_text("NONCE-not-json")
        unusable = world.run(surface).out

        assert marker_write_clause in listed
        assert ("pending, and still in force" in listed) is (surface == "status")
        assert "recorded as the sandbox grant, and 0 of them are listed" in none_listed
        assert "how many are listed now is unknown" in unusable
        for out in (none_listed, unusable):
            assert marker_write_clause not in out
            assert "still in force" not in out
            assert "`uninstall` removes the grant" not in out
        assert "NONCE" not in unusable

    @pytest.mark.parametrize("hooks_state", ["absent", "unparseable"])
    @pytest.mark.parametrize("surface", ["status", "check"])
    def test_the_sandbox_comparison_runs_whatever_the_state_of_hooks_json(
        self, grant_world: World, monkeypatch: pytest.MonkeyPatch, surface: str, hooks_state: str
    ) -> None:
        world = grant_world
        world.install()
        grant_parity(world, monkeypatch, pending=True)
        sandbox_file(world).unlink()
        if hooks_state == "absent":
            world.hooks_path.unlink()
        else:
            world.hooks_path.write_text("{not json")

        out = world.run(surface).out

        assert "sandbox.json is missing, so the sandbox grant is gone" in out
        assert "recorded as the sandbox grant" in out

    @pytest.mark.skipif(IS_ROOT, reason="absent: root reads through mode bits")
    def test_an_unreadable_saved_pre_install_sandbox_file_is_a_finding_that_names_it_and_check_still_finishes(
        self, grant_world: World
    ) -> None:
        world = grant_world
        sandbox_file(world).write_bytes(ENGINEER_SANDBOX)
        world.install()
        pre_image = world.record_dir / "sandbox.json.pre-install"
        pre_image.chmod(0)
        try:
            result = world.run("check")
        finally:
            pre_image.chmod(0o600)

        assert "sandbox.json.pre-install cannot be read (EACCES)" in result.out
        assert "failed on an unexpected" not in result.err

    def test_a_pending_prerequisite_with_no_recorded_grant_reports_no_recorded_grant_finding(
        self, fast_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = fast_world
        grant_parity(world, monkeypatch, pending=True)
        world.install()

        for surface in ("status", "check"):
            assert "recorded as the sandbox grant" not in world.run(surface).out

    def test_the_repositorys_own_parity_file_keeps_the_grant_pending_so_install_writes_no_sandbox_file_record_or_directory(
        self, fast_world: World
    ) -> None:
        # The one unseamed row: the repository's own parity file still lists the permissions gate, so this row changes when
        # that entry is removed. Every other row injects its own parity file.
        world = fast_world

        assert world.install().status == 0

        assert not sandbox_file(world).exists()
        assert [path.name for path in world.record_dir.iterdir() if "sandbox" in path.name] == []
        assert not any((world.config_dir / name).exists() for name in world.adapter.SANDBOX_WRITABLE_DIRECTORIES)
        assert world.adapter.SANDBOX_GRANT_PENDING_LINE in world.run("status").out


class TestGrantPending:
    """`grant_pending` calls the grant delivered only on a well-formed parity file with no exception entry for the gate."""

    GATE_ENTRY = '{"kind": "adapter-gate", "key": "gate-settings-permissions.sh"%s}'

    def _parity(self, world: World, monkeypatch: pytest.MonkeyPatch, content: str | None) -> None:
        path = world.tmp_path / "grant-parity.json"
        if content is not None:
            path.write_text(content)
        monkeypatch.setattr(world.adapter, "grant_exceptions_path", lambda: str(path))

    @pytest.mark.parametrize(
        "content",
        [
            None,
            "{not json",
            "[]",
            "{}",
            '{"entries": null}',
            '{"entries": {"kind": "adapter-gate"}}',
            '{"entries": "none"}',
            '{"entries": [{"kind": "registration", "key": "x"}, "text"]}',
            '{"entries": [1]}',
            '{"entries": [%s]}' % (GATE_ENTRY % ', "reason": "pending-phase"'),
            '{"entries": [%s]}' % (GATE_ENTRY % ', "reason": "withheld-premise"'),
            '{"entries": [%s]}' % (GATE_ENTRY % ', "reason": "declined"'),
            '{"entries": [%s]}' % (GATE_ENTRY % ', "reason": "pending-phsae"'),
            '{"entries": [%s]}' % (GATE_ENTRY % ""),
        ],
        ids=[
            "unreadable-absent-file",
            "unparseable",
            "document-is-a-list",
            "entries-missing",
            "entries-null",
            "entries-an-object",
            "entries-a-string",
            "an-entry-is-a-string",
            "an-entry-is-a-number",
            "reason-pending-phase",
            "reason-withheld-premise",
            "reason-declined",
            "reason-typo",
            "reason-absent",
        ],
    )
    def test_the_grant_stays_pending_for_an_unreadable_or_malformed_file_and_for_any_exception_entry_of_the_gate(
        self, world: World, monkeypatch: pytest.MonkeyPatch, content: str | None
    ) -> None:
        self._parity(world, monkeypatch, content)

        assert world.adapter.grant_pending() is True

    @pytest.mark.parametrize(
        "content",
        ['{"entries": []}', '{"entries": [{"kind": "adapter-gate", "key": "gate-agent-tools.sh", "reason": "pending-phase"}]}'],
        ids=["no-entries", "only-other-gates"],
    )
    def test_the_grant_is_delivered_on_a_well_formed_file_with_no_exception_for_the_permissions_gate(
        self, world: World, monkeypatch: pytest.MonkeyPatch, content: str
    ) -> None:
        self._parity(world, monkeypatch, content)

        assert world.adapter.grant_pending() is False
