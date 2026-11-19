"""cursor-adapter.py inputs it cannot use: a damaged record, config directory, manifest key, planted file or file shape."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import types
from pathlib import Path

import pytest
from helpers import CLAUDE_DIR

from . import _cursor_install_helpers as install_helpers
from ._cursor_install_helpers import (
    HOOK_ENV_SURFACE,
    ORIGINAL_HOOKS,
    SURFACES,
    Result,
    World,
    load_adapter_copy_with,
    run_surface,
    use_settings,
)

sys.path.insert(0, str(CLAUDE_DIR))
from hooks.tests._cursor_payloads import (  # noqa: E402 (the import needs the sys.path entry above)
    IS_ROOT,
    wrapper_name,
    write_sh_script,
)

# The fixtures live once in the helper module. An import would trip F811 on every test parameter that names one.
world = install_helpers.world
fast_world = install_helpers.fast_world


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
