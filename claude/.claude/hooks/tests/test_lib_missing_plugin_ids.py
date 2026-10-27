"""Unit tests for _lib.sh's _lib_missing_plugin_ids, _lib_file_exceeds_size_cap and _lib_read_guarded_settings."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest
from helpers import (
    _sourced_value,
    v2_registry_entry,
    write_plugin_registry,
)

from ._lib_sh_helpers import (
    _run_lib_call,
)


def _missing_plugin_ids(settings_file: Path, registry_file: Path) -> subprocess.CompletedProcess:
    """Runs under `set -o pipefail`, the mode the reconciler runs in."""
    return _run_lib_call(
        "set -o pipefail; _lib_missing_plugin_ids "
        f"{shlex.quote(str(settings_file))} {shlex.quote(str(registry_file))}",
        env=dict(os.environ),
    )


class TestLibFileExceedsSizeCap:
    @staticmethod
    def _exceeds_size_cap(file_path: Path) -> subprocess.CompletedProcess:
        return _run_lib_call(
            f"_lib_file_exceeds_size_cap {shlex.quote(str(file_path))}", env=dict(os.environ)
        )

    def test_file_of_exactly_the_threshold_is_not_exceeded(self, tmp_path: Path) -> None:
        threshold_bytes = int(_sourced_value("_LIB_SIZE_THRESHOLD_BYTES"))
        sized_file = tmp_path / "at-threshold.json"
        sized_file.write_bytes(b"x" * threshold_bytes)

        assert self._exceeds_size_cap(sized_file).returncode == 1

    def test_file_one_byte_over_the_threshold_is_exceeded(self, tmp_path: Path) -> None:
        threshold_bytes = int(_sourced_value("_LIB_SIZE_THRESHOLD_BYTES"))
        sized_file = tmp_path / "over-threshold.json"
        sized_file.write_bytes(b"x" * (threshold_bytes + 1))

        assert self._exceeds_size_cap(sized_file).returncode == 0

    def test_unknown_size_is_not_exceeded(self, tmp_path: Path) -> None:
        """Polarity: a size that cannot be determined (here, a nonexistent
        file) reports "not exceeded" -- callers decide separately what an
        unreadable file means, so this predicate never doubles as a deny."""
        assert self._exceeds_size_cap(tmp_path / "no-such-file.json").returncode == 1


class TestLibMissingPluginIds:
    @staticmethod
    def _settings_enabling_ids(tmp_path: Path, id_count: int) -> Path:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(
            json.dumps({"enabledPlugins": {f"plugin-{index}@market": True for index in range(id_count)}})
        )
        return settings_file

    def test_reports_every_missing_id_when_enabled_count_is_exactly_the_cap(self, tmp_path: Path) -> None:
        enabled_ids_max = int(_sourced_value("_LIB_ENABLED_PLUGIN_IDS_MAX"))
        settings_file = self._settings_enabling_ids(tmp_path, enabled_ids_max)

        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")

        assert result.returncode == 0, repr(result)
        assert len(result.stdout.split()) == enabled_ids_max

    def test_refuses_with_status_1_and_empty_stdout_one_id_past_the_cap(self, tmp_path: Path) -> None:
        """Guards the per-id `grep` fork count and downstream validation work
        against a settings file that enables an unbounded number of ids."""
        enabled_ids_max = int(_sourced_value("_LIB_ENABLED_PLUGIN_IDS_MAX"))
        settings_file = self._settings_enabling_ids(tmp_path, enabled_ids_max + 1)

        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")

        assert result.returncode == 1, repr(result)
        assert result.stdout == ""

    def test_reports_every_true_valued_id_when_registry_is_absent(self, tmp_path: Path) -> None:
        """Matches install.sh's registry-read step ("proceeding as if none
        are installed") -- a missing registry is treated as an empty one."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True, "baz@qux": True}}))
        registry_file = tmp_path / "installed_plugins.json"

        result = _missing_plugin_ids(settings_file, registry_file)

        assert result.returncode == 0, repr(result)
        assert sorted(result.stdout.split()) == ["baz@qux", "foo@bar"]

    def test_prints_nothing_when_settings_file_is_absent(self, tmp_path: Path) -> None:
        result = _missing_plugin_ids(tmp_path / "no-such-settings.json", tmp_path / "no-such-registry.json")
        assert result.returncode == 0, repr(result)
        assert result.stdout.strip() == ""

    def test_prints_nothing_when_enabled_plugins_is_absent(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text("{}")
        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")
        assert result.returncode == 0, repr(result)
        assert result.stdout.strip() == ""

    def test_skips_false_valued_entries(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": False}}))
        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")
        assert result.returncode == 0, repr(result)
        assert result.stdout.strip() == ""

    @pytest.mark.parametrize(
        "non_boolean_value",
        ["true", 1, None, [], {}, "false"],
        ids=["string-true", "number-one", "null", "empty-array", "empty-object", "string-false"],
    )
    def test_skips_entries_whose_value_is_not_the_boolean_true(self, tmp_path: Path, non_boolean_value) -> None:
        """Only the JSON boolean `true` enables a plugin, so a truthy-looking or empty value does not."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": non_boolean_value}}))
        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")
        assert result.returncode == 0, repr(result)
        assert result.stdout == ""

    def test_id_already_in_registry_is_not_reported_missing(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
        registry_file = tmp_path / "installed_plugins.json"
        write_plugin_registry(registry_file, {"foo@bar": [v2_registry_entry("/repo-a")]})

        result = _missing_plugin_ids(settings_file, registry_file)

        assert result.returncode == 0, repr(result)
        assert result.stdout.strip() == ""

    def test_registry_id_sharing_a_prefix_does_not_count_as_installed(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
        registry_file = tmp_path / "installed_plugins.json"
        write_plugin_registry(registry_file, {"foo@bar-extra": [v2_registry_entry("/repo-a")]})

        result = _missing_plugin_ids(settings_file, registry_file)

        assert result.returncode == 0, repr(result)
        assert result.stdout.split() == ["foo@bar"]

    def test_registry_regex_lookalike_id_does_not_count_as_installed(self, tmp_path: Path) -> None:
        """The enabled `a.b@c` carries the regex metacharacter, so a
        non-fixed-string match would treat the registry's `axb@c` as it."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"a.b@c": True}}))
        registry_file = tmp_path / "installed_plugins.json"
        write_plugin_registry(registry_file, {"axb@c": [v2_registry_entry("/repo-a")]})

        result = _missing_plugin_ids(settings_file, registry_file)

        assert result.returncode == 0, repr(result)
        assert result.stdout.split() == ["a.b@c"]

    def test_resolution_is_name_keyed_not_path_keyed(self, tmp_path: Path) -> None:
        """A registry entry recorded under a different projectPath than the
        settings file's own repo still counts as installed. Presence is
        name-keyed, and must not regress toward install.sh's projectPath-strict
        idempotence, which would reinstall per worktree."""
        settings_file = tmp_path / "repo-b" / ".claude" / "settings.json"
        settings_file.parent.mkdir(parents=True)
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
        registry_file = tmp_path / "installed_plugins.json"
        write_plugin_registry(registry_file, {"foo@bar": [v2_registry_entry("/repo-a")]})

        result = _missing_plugin_ids(settings_file, registry_file)

        assert result.returncode == 0, repr(result)
        assert result.stdout.strip() == ""

    def test_registry_present_but_unparseable_fails_closed(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
        registry_file = tmp_path / "installed_plugins.json"
        registry_file.write_text("{not valid json")

        result = _missing_plugin_ids(settings_file, registry_file)

        assert result.returncode == 2, repr(result)
        assert result.stdout.strip() == ""

    @pytest.mark.parametrize("registry_version", [None, 1, 3], ids=["version-missing", "version-1", "version-3"])
    def test_registry_version_mismatch_fails_closed(self, tmp_path: Path, registry_version: int | None) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
        registry_file = tmp_path / "installed_plugins.json"
        write_plugin_registry(registry_file, {}, version=registry_version)

        result = _missing_plugin_ids(settings_file, registry_file)

        assert result.returncode == 2, repr(result)
        assert result.stdout.strip() == ""

    def test_skipped_when_settings_file_is_a_symlink(self, tmp_path: Path) -> None:
        real_target = tmp_path / "real-settings.json"
        real_target.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
        settings_file = tmp_path / "settings.json"
        settings_file.symlink_to(real_target)

        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")

        assert result.returncode == 1, repr(result)
        assert result.stdout.strip() == ""

    def test_skipped_when_settings_file_is_not_a_regular_file(self, tmp_path: Path) -> None:
        """A directory in place of the settings file -- the non-symlink half
        of the "regular file, not a symlink" guard."""
        settings_file = tmp_path / "settings.json"
        settings_file.mkdir()

        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")

        assert result.returncode == 1, repr(result)
        assert result.stdout.strip() == ""

    def test_skipped_when_settings_file_exceeds_size_cap(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "settings.json"
        oversized_padding = " " * (5 * 1024 * 1024 + 1)
        settings_file.write_text(
            json.dumps({"enabledPlugins": {"foo@bar": True}, "padding": oversized_padding})
        )

        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")

        assert result.returncode == 1, repr(result)
        assert result.stdout.strip() == ""


class TestLibReadGuardedSettings:
    @staticmethod
    def _read(settings_file: Path) -> subprocess.CompletedProcess:
        return _run_lib_call(
            f"set -o pipefail; _lib_read_guarded_settings {shlex.quote(str(settings_file))}",
            env=dict(os.environ),
        )

    def test_prints_the_content_of_a_regular_file(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text('{"enabledPlugins": {"foo@bar": true}}')

        result = self._read(settings_file)

        assert result.returncode == 0, repr(result)
        assert result.stdout == '{"enabledPlugins": {"foo@bar": true}}'

    def test_absent_file_prints_nothing_and_succeeds(self, tmp_path: Path) -> None:
        result = self._read(tmp_path / "no-such-settings.json")

        assert result.returncode == 0, repr(result)
        assert result.stdout == ""

    def test_refuses_a_symlink(self, tmp_path: Path) -> None:
        real_target = tmp_path / "real-settings.json"
        real_target.write_text("{}")
        settings_file = tmp_path / "settings.json"
        settings_file.symlink_to(real_target)

        result = self._read(settings_file)

        assert result.returncode == 1, repr(result)
        assert result.stdout == ""

    def test_refuses_a_directory(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.mkdir()

        result = self._read(settings_file)

        assert result.returncode == 1, repr(result)
        assert result.stdout == ""

    def test_refuses_a_file_over_the_size_cap(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.write_bytes(b" " * (int(_sourced_value("_LIB_SIZE_THRESHOLD_BYTES")) + 1))

        result = self._read(settings_file)

        assert result.returncode == 1, repr(result)
        assert result.stdout == ""

    @pytest.mark.parametrize(
        "settings_bytes",
        [b'{"enabledPlugins": {"foo@bar": true}}\x00', b'{"enabledPlugins": {"foo@bar":\x00 true}}', b"\x00"],
        ids=["trailing-nul", "embedded-nul", "only-nul"],
    )
    def test_refuses_content_holding_a_nul_byte(self, tmp_path: Path, settings_bytes: bytes) -> None:
        """Command substitution drops NUL silently, so an unrefused NUL would hand the caller bytes that
        differ from the file and that a strict JSON parser rejects."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_bytes(settings_bytes)

        result = self._read(settings_file)

        assert result.returncode == 1, repr(result)
        assert result.stdout == ""

    def test_nul_in_settings_is_refused_by_the_missing_id_helper_too(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.write_bytes(b'{"enabledPlugins": {"foo@bar": true}}\x00')

        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")

        assert result.returncode == 1, repr(result)
        assert result.stdout == ""

    def test_content_without_nul_but_with_other_control_bytes_is_printed_unchanged(self, tmp_path: Path) -> None:
        """Allow control for the NUL refusal: tab, newline, and a raw 0x02 byte (invalid JSON but not NUL) pass through."""
        settings_text = '{"enabledPlugins":\t{"foo@bar": true}}\n\x02'
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(settings_text)

        result = self._read(settings_file)

        assert result.returncode == 0, repr(result)
        assert result.stdout == settings_text

    def _read_with_size_precheck_disabled(self, settings_file: Path) -> subprocess.CompletedProcess:
        """Overrides the stat-based pre-check, so only the capped read and its length re-check can refuse."""
        return _run_lib_call(
            f"_lib_file_exceeds_size_cap() {{ return 1; }}; set -o pipefail; "
            f"_lib_read_guarded_settings {shlex.quote(str(settings_file))}",
            env=dict(os.environ),
        )

    def test_a_file_that_outgrows_the_precheck_is_refused_by_the_capped_read(self, tmp_path: Path) -> None:
        """A path swapped to a larger file after the size pre-check is read through `head -c` and re-measured."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_bytes(b" " * (int(_sourced_value("_LIB_SIZE_THRESHOLD_BYTES")) + 1))

        result = self._read_with_size_precheck_disabled(settings_file)

        assert result.returncode == 1, repr(result)
        assert result.stdout == ""

    def test_a_file_of_exactly_the_cap_passes_the_capped_read(self, tmp_path: Path) -> None:
        """Allow control for the capped-read refusal: the bound is inclusive."""
        threshold_bytes = int(_sourced_value("_LIB_SIZE_THRESHOLD_BYTES"))
        settings_file = tmp_path / "settings.json"
        settings_file.write_bytes(b" " * threshold_bytes)

        result = self._read_with_size_precheck_disabled(settings_file)

        assert result.returncode == 0, repr(result)
        assert len(result.stdout) == threshold_bytes

    def test_reads_through_a_symlinked_claude_directory(self, tmp_path: Path) -> None:
        """The symlink guard covers the final path component only, so a
        symlinked `.claude` directory is read. The reconciler's write-side
        git check, not this guard, refuses a repo whose `.claude` is a symlink."""
        real_claude_dir = tmp_path / "real-claude-dir"
        real_claude_dir.mkdir()
        (real_claude_dir / "settings.json").write_text('{"enabledPlugins": {"foo@bar": true}}')
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".claude").symlink_to(real_claude_dir)

        result = self._read(repo / ".claude" / "settings.json")

        assert result.returncode == 0, repr(result)
        assert "foo@bar" in result.stdout


class TestLibMissingPluginIdsInputShape:
    """One output record per `enabledPlugins` key, one top-level JSON value, and a strictly shaped registry."""

    @staticmethod
    def _missing_ids_for_settings_text(tmp_path: Path, settings_text: str) -> subprocess.CompletedProcess:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(settings_text)
        return _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")

    def test_key_containing_a_newline_is_dropped_not_split_into_two_ids(self, tmp_path: Path) -> None:
        settings_text = json.dumps({"enabledPlugins": {"x@y\nz@w": True, "foo@bar": True}})

        result = self._missing_ids_for_settings_text(tmp_path, settings_text)

        assert result.returncode == 0, repr(result)
        assert result.stdout.split("\n") == ["foo@bar", ""]

    @pytest.mark.parametrize("control_character", ["\x01", "\x1b", "\x7f", "\u009b", "\t"])
    def test_key_containing_a_control_character_is_dropped(self, tmp_path: Path, control_character: str) -> None:
        settings_text = json.dumps(
            {"enabledPlugins": {f"foo{control_character}bar@baz": True, "keep@market": True}}
        )

        result = self._missing_ids_for_settings_text(tmp_path, settings_text)

        assert result.returncode == 0, repr(result)
        assert result.stdout.split("\n") == ["keep@market", ""]

    def test_two_top_level_json_values_are_refused_with_status_1(self, tmp_path: Path) -> None:
        settings_text = '{"enabledPlugins": {"foo@bar": true}} {"enabledPlugins": {"baz@qux": true}}'

        result = self._missing_ids_for_settings_text(tmp_path, settings_text)

        assert result.returncode == 1, repr(result)
        assert result.stdout == ""

    def test_single_top_level_value_followed_by_whitespace_is_accepted(self, tmp_path: Path) -> None:
        result = self._missing_ids_for_settings_text(
            tmp_path, '{"enabledPlugins": {"foo@bar": true}}\n\n  \n'
        )

        assert result.returncode == 0, repr(result)
        assert result.stdout.split() == ["foo@bar"]

    def test_content_that_is_not_json_prints_nothing_and_succeeds(self, tmp_path: Path) -> None:
        result = self._missing_ids_for_settings_text(tmp_path, "not json at all")

        assert result.returncode == 0, repr(result)
        assert result.stdout == ""

    def test_symlinked_claude_directory_is_read_through(self, tmp_path: Path) -> None:
        real_claude_dir = tmp_path / "real-claude-dir"
        real_claude_dir.mkdir()
        (real_claude_dir / "settings.json").write_text('{"enabledPlugins": {"foo@bar": true}}')
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".claude").symlink_to(real_claude_dir)

        result = _missing_plugin_ids(repo / ".claude" / "settings.json", tmp_path / "no-such-registry.json")

        assert result.returncode == 0, repr(result)
        assert result.stdout.split() == ["foo@bar"]

    def test_content_argument_wins_over_whatever_the_settings_path_now_holds(self, tmp_path: Path) -> None:
        """The reconciler hashes the bytes it validated, so the missing-id set
        must come from those bytes, not from a second read of the path."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text('{"enabledPlugins": {"swapped@later": true}}')
        content = '{"enabledPlugins": {"validated@earlier": true}}'

        result = _run_lib_call(
            "set -o pipefail; _lib_missing_plugin_ids_for_content "
            f"{shlex.quote(content)} {shlex.quote(str(settings_file))} {shlex.quote(str(tmp_path / 'no-such-registry.json'))}",
            env=dict(os.environ),
        )

        assert result.returncode == 0, repr(result)
        assert result.stdout.split() == ["validated@earlier"]

    @staticmethod
    def _registry_status(tmp_path: Path, registry_text: str) -> subprocess.CompletedProcess:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
        registry_file = tmp_path / "installed_plugins.json"
        registry_file.write_text(registry_text)
        return _missing_plugin_ids(settings_file, registry_file)

    @pytest.mark.parametrize(
        "registry_text",
        [
            '{"version": "2", "plugins": {}}',
            '{"version": 2}',
            '{"version": 2, "plugins": null}',
            '{"version": 2, "plugins": []}',
            '{"version": 2, "plugins": "x"}',
            '[]',
            "",
            '{"version": 2, "plugins": {}} {"version": 2, "plugins": {}}',
        ],
        ids=[
            "version-is-a-string",
            "plugins-absent",
            "plugins-null",
            "plugins-array",
            "plugins-string",
            "top-level-array",
            "empty-file",
            "two-top-level-values",
        ],
    )
    def test_registry_that_is_not_a_version_2_object_with_an_object_of_plugins_fails_closed(
        self, tmp_path: Path, registry_text: str
    ) -> None:
        result = self._registry_status(tmp_path, registry_text)

        assert result.returncode == 2, repr(result)
        assert result.stdout == ""

    def test_version_2_registry_with_an_empty_plugins_object_reports_the_id_missing(self, tmp_path: Path) -> None:
        result = self._registry_status(tmp_path, '{"version": 2, "plugins": {}}')

        assert result.returncode == 0, repr(result)
        assert result.stdout.split() == ["foo@bar"]


class TestLibMissingPluginIdsLocalOptOut:
    """A sibling settings.local.json `false` is a per-repo opt-out.

    Shape policy: an absent, empty, or whitespace-only file, a top-level
    `null`, and an object whose `enabledPlugins` is absent or `null` mean no
    opt-outs, so ids stay eligible, as does an `enabledPlugins` object. A
    malformed or wrong-shaped file is unreadable (the full definition is in
    the `_lib_missing_plugin_ids` header in `_lib.sh`) and fails closed with
    exit status 3, but only when an id would otherwise be
    reported; a registry that already holds every enabled id never reads the
    local file.
    """

    @staticmethod
    def _missing_ids_result_with_local_settings(
        tmp_path: Path, local_settings_text: str | None
    ) -> subprocess.CompletedProcess:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True, "baz@qux": True}}))
        if local_settings_text is not None:
            (tmp_path / "settings.local.json").write_text(local_settings_text)

        return _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")

    @classmethod
    def _missing_ids_with_local_settings(cls, tmp_path: Path, local_settings_text: str | None) -> list[str]:
        result = cls._missing_ids_result_with_local_settings(tmp_path, local_settings_text)

        assert result.returncode == 0, repr(result)
        return sorted(result.stdout.split())

    def test_local_false_drops_only_that_id(self, tmp_path: Path) -> None:
        local_settings = json.dumps({"enabledPlugins": {"foo@bar": False}})
        assert self._missing_ids_with_local_settings(tmp_path, local_settings) == ["baz@qux"]

    def test_local_true_keeps_the_id(self, tmp_path: Path) -> None:
        local_settings = json.dumps({"enabledPlugins": {"foo@bar": True}})
        assert self._missing_ids_with_local_settings(tmp_path, local_settings) == ["baz@qux", "foo@bar"]

    def test_id_absent_from_local_file_keeps_the_id(self, tmp_path: Path) -> None:
        local_settings = json.dumps({"enabledPlugins": {"other@market": False}})
        assert self._missing_ids_with_local_settings(tmp_path, local_settings) == ["baz@qux", "foo@bar"]

    def test_local_file_without_enabled_plugins_keeps_the_id(self, tmp_path: Path) -> None:
        assert self._missing_ids_with_local_settings(tmp_path, "{}") == ["baz@qux", "foo@bar"]

    def test_absent_local_file_keeps_the_id(self, tmp_path: Path) -> None:
        assert self._missing_ids_with_local_settings(tmp_path, None) == ["baz@qux", "foo@bar"]

    @pytest.mark.parametrize("non_false_value", ["false", None, 0, ""])
    def test_local_value_other_than_boolean_false_keeps_the_id(
        self, tmp_path: Path, non_false_value: object
    ) -> None:
        local_settings = json.dumps({"enabledPlugins": {"foo@bar": non_false_value}})
        assert self._missing_ids_with_local_settings(tmp_path, local_settings) == ["baz@qux", "foo@bar"]

    def test_unparseable_local_file_fails_closed(self, tmp_path: Path) -> None:
        result = self._missing_ids_result_with_local_settings(tmp_path, "{not valid json")

        assert result.returncode == 3, repr(result)
        assert result.stdout.strip() == ""

    @pytest.mark.parametrize("no_opt_out_text", ["", "   \n\t\n", "null", '{"enabledPlugins": null}'])
    def test_empty_or_null_local_shapes_mean_no_opt_outs(
        self, tmp_path: Path, no_opt_out_text: str
    ) -> None:
        assert self._missing_ids_with_local_settings(tmp_path, no_opt_out_text) == ["baz@qux", "foo@bar"]

    @pytest.mark.parametrize(
        "unreadable_text",
        [
            "[]",
            '"x"',
            "5",
            "true",
            '{"enabledPlugins": [false]}',
            '{"enabledPlugins": "foo@bar"}',
            '{"enabledPlugins": false}',
            '{"enabledPlugins": 5}',
        ],
        ids=[
            "top-level-array",
            "top-level-string",
            "top-level-number",
            "top-level-boolean",
            "enabled-plugins-array",
            "enabled-plugins-string",
            "enabled-plugins-false",
            "enabled-plugins-number",
        ],
    )
    def test_wrong_shape_local_file_fails_closed(self, tmp_path: Path, unreadable_text: str) -> None:
        result = self._missing_ids_result_with_local_settings(tmp_path, unreadable_text)

        assert result.returncode == 3, repr(result)
        assert result.stdout.strip() == ""

    def test_local_false_for_a_longer_id_sharing_a_prefix_keeps_the_id(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
        (tmp_path / "settings.local.json").write_text(
            json.dumps({"enabledPlugins": {"foo@bar-extra": False}})
        )

        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")

        assert result.returncode == 0, repr(result)
        assert result.stdout.split() == ["foo@bar"]

    def test_local_false_for_a_regex_lookalike_id_keeps_the_id(self, tmp_path: Path) -> None:
        """Membership is a fixed-string whole-line match, so a local `false`
        for `axb@c` must not suppress the enabled `a.b@c`, whose `.` would
        match it as a regex."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"a.b@c": True}}))
        (tmp_path / "settings.local.json").write_text(
            json.dumps({"enabledPlugins": {"axb@c": False}})
        )

        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")

        assert result.returncode == 0, repr(result)
        assert result.stdout.split() == ["a.b@c"]

    def test_settings_local_json_in_the_parent_directory_is_ignored(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "repo" / ".claude" / "settings.json"
        settings_file.parent.mkdir(parents=True)
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
        (tmp_path / "repo" / "settings.local.json").write_text(
            json.dumps({"enabledPlugins": {"foo@bar": False}})
        )

        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")

        assert result.returncode == 0, repr(result)
        assert result.stdout.split() == ["foo@bar"]

    def test_match_early_in_a_large_local_false_list_still_drops_the_id(self, tmp_path: Path) -> None:
        """Under pipefail, a `printf | grep -q` membership test fails once grep
        exits on an early match and the printf is killed by SIGPIPE; the id
        would then wrongly be reported. The id list must outgrow the 64 KiB
        pipe buffer for the writer to still be writing when grep exits."""
        pipe_buffer_bytes = 64 * 1024
        opt_outs = {"foo@bar": False}
        id_list_bytes = len("foo@bar\n")
        while id_list_bytes < 2 * pipe_buffer_bytes:
            filler_id = f"k{len(opt_outs)}@m"
            opt_outs[filler_id] = False
            id_list_bytes += len(filler_id) + 1
        local_settings = json.dumps({"enabledPlugins": opt_outs}, separators=(",", ":"))

        assert self._missing_ids_with_local_settings(tmp_path, local_settings) == ["baz@qux"]

    def test_symlinked_local_file_fails_closed(self, tmp_path: Path) -> None:
        real_target = tmp_path / "real-local.json"
        real_target.write_text(json.dumps({"enabledPlugins": {"foo@bar": False}}))
        (tmp_path / "settings.local.json").symlink_to(real_target)
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))

        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")

        assert result.returncode == 3, repr(result)
        assert result.stdout.strip() == ""

    def test_dangling_symlinked_local_file_fails_closed(self, tmp_path: Path) -> None:
        (tmp_path / "settings.local.json").symlink_to(tmp_path / "no-such-target.json")
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))

        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")

        assert result.returncode == 3, repr(result)
        assert result.stdout.strip() == ""

    def test_non_regular_local_path_fails_closed(self, tmp_path: Path) -> None:
        (tmp_path / "settings.local.json").mkdir()
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))

        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")

        assert result.returncode == 3, repr(result)
        assert result.stdout.strip() == ""

    def test_oversized_local_file_fails_closed(self, tmp_path: Path) -> None:
        oversized_padding = " " * (5 * 1024 * 1024 + 1)
        local_settings = json.dumps(
            {"enabledPlugins": {"foo@bar": False}, "padding": oversized_padding}
        )

        result = self._missing_ids_result_with_local_settings(tmp_path, local_settings)

        assert result.returncode == 3, repr(result)
        assert result.stdout.strip() == ""

    def test_local_false_does_not_enable_an_id_absent_from_checked_in_file(self, tmp_path: Path) -> None:
        """The local file only subtracts; it never adds an id."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
        (tmp_path / "settings.local.json").write_text(
            json.dumps({"enabledPlugins": {"baz@qux": True}})
        )

        result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")

        assert result.returncode == 0, repr(result)
        assert result.stdout.split() == ["foo@bar"]

    def test_all_ids_installed_never_reads_an_unreadable_local_file(self, tmp_path: Path) -> None:
        (tmp_path / "settings.local.json").write_text("{not valid json")
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
        registry_file = tmp_path / "installed_plugins.json"
        write_plugin_registry(registry_file, {"foo@bar": [v2_registry_entry("/repo-a")]})

        result = _missing_plugin_ids(settings_file, registry_file)

        assert result.returncode == 0, repr(result)
        assert result.stdout == ""

    def test_something_missing_with_an_unreadable_local_file_fails_closed(self, tmp_path: Path) -> None:
        (tmp_path / "settings.local.json").write_text("{not valid json")
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True, "baz@qux": True}}))
        registry_file = tmp_path / "installed_plugins.json"
        write_plugin_registry(registry_file, {"foo@bar": [v2_registry_entry("/repo-a")]})

        result = _missing_plugin_ids(settings_file, registry_file)

        assert result.returncode == 3, repr(result)
        assert result.stdout == ""

    def test_corrupt_registry_takes_precedence_over_an_unreadable_local_file(self, tmp_path: Path) -> None:
        (tmp_path / "settings.local.json").write_text("{not valid json")
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
        registry_file = tmp_path / "installed_plugins.json"
        registry_file.write_text("{not valid json")

        result = _missing_plugin_ids(settings_file, registry_file)

        assert result.returncode == 2, repr(result)
        assert result.stdout == ""

    @pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file")
    def test_permission_denied_local_file_fails_closed(self, tmp_path: Path) -> None:
        local_settings_file = tmp_path / "settings.local.json"
        local_settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": False}}))
        local_settings_file.chmod(0o000)
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))

        try:
            result = _missing_plugin_ids(settings_file, tmp_path / "no-such-registry.json")
        finally:
            local_settings_file.chmod(0o600)

        assert result.returncode == 3, repr(result)
        assert result.stdout == ""
