"""Direct tests for the readiness, reason-table and account-directory half of _branch-records-lib.sh.

The lib is sourced standalone in `bash -c`. The snapshot half is in test_branch_records_archive.py.
Neither cleanup script is run here: the input matrix for the archive lives at this seam, and the
script tests hold one case per branch. Every command starts with CDPATH set to a real directory and
umask 022, and every call's report asserts both unchanged along with cwd and an empty stdout.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import stat
from pathlib import Path

import pytest
from helpers import (
    REPO_ROOT,
    assert_cap_engaged,
    scaled_shim_sleep,
    write_scaled_timeout_shim,
)

from . import _branch_records_helpers as records_helpers
from ._branch_records_helpers import (
    ENABLING_LINE,
    SENTINEL_NAME,
    account_component,
    account_directory,
    archive_root,
    archive_successfully,
    assert_lib_env_confined,
    assert_script_env_confined,
    build_archive_env,
    list_tree,
    read_only,
    reason_variables,
    requires_non_root,
    run_archive,
    run_lib,
    run_status,
    shim_path_env,
    write_cp_shim,
    write_shim,
    write_tree,
)

# The fixtures live once in the helper module. An import would trip F811 on every test parameter that names one.
reason = records_helpers.reason
archive_worktree = records_helpers.archive_worktree
status_shell_options = records_helpers.status_shell_options
process_isolation = records_helpers.process_isolation

_DOC = REPO_ROOT / "docs" / "agent-branch-records.md"
_EXPECTED_REASON_CAUSES = {
    "CONFIG", "SENTINEL", "STALLED", "ROOT_NOT_WRITABLE", "ACCOUNT_NOT_WRITABLE", "LEDGER_KEY",
    "UNSUPPORTED_ENTRY", "DIRECTORY", "SNAPSHOT_MKDIR", "COPY_FAILED", "COPY_MISMATCH", "MANIFEST", "KEY_UNREADABLE",
}
_COMMANDS_THAT_CREATE_THE_ROOT_OR_SENTINEL = {"touch", "mkdir", "ln"}
_UTF8_BOM = b"\xef\xbb\xbf"
_FALSE_LINE = "agent_branch_records = false"


class TestProcessIsolation:
    def test_autouse_fixture_starts_each_case_in_its_own_tmp_path(self, tmp_path):
        assert Path.cwd() == tmp_path


class TestEnvironmentConfinement:
    @pytest.mark.parametrize(
        ("variable", "escape"),
        [("HOME", "outside"), ("HOME", "unset"), ("CLAUDE_CONFIG_DIR", "outside")],
    )
    def test_run_lib_refuses_an_environment_that_could_reach_the_real_archive(self, tmp_path, variable, escape):
        env = build_archive_env(tmp_path)
        if escape == "outside":
            env[variable] = str(tmp_path.parent)
        else:
            del env[variable]

        with pytest.raises(AssertionError, match=variable):
            run_lib(tmp_path, env, "call_status")

        assert not (tmp_path / "call_stdout").exists(), "the subprocess started before the check"

    @pytest.mark.parametrize("home", ["outside", "unset"])
    def test_run_lib_refuses_an_unset_config_dir_unless_home_is_under_tmp_path(self, tmp_path, home):
        env = build_archive_env(tmp_path)
        del env["CLAUDE_CONFIG_DIR"]
        if home == "outside":
            env["HOME"] = str(tmp_path.parent)
        else:
            del env["HOME"]

        with pytest.raises(AssertionError, match="HOME"):
            run_lib(tmp_path, env, "call_status")

        assert not (tmp_path / "call_stdout").exists(), "the subprocess started before the check"

    @pytest.mark.parametrize("config_dir_state", ["unset", "empty"])
    def test_run_lib_refuses_a_default_config_dir_that_resolves_outside_tmp_path(self, tmp_path, config_dir_state):
        env = build_archive_env(tmp_path)
        if config_dir_state == "unset":
            del env["CLAUDE_CONFIG_DIR"]
        else:
            env["CLAUDE_CONFIG_DIR"] = ""
        (Path(env["HOME"]) / ".claude").symlink_to(tmp_path.parent)

        with pytest.raises(AssertionError, match="default config dir"):
            run_lib(tmp_path, env, "call_status")

        assert not (tmp_path / "call_stdout").exists(), "the subprocess started before the check"

    @pytest.mark.parametrize("config_dir_state", ["unset", "empty"])
    def test_assert_lib_env_confined_accepts_an_unset_or_empty_config_dir_when_home_is_under_tmp_path(
        self, tmp_path, config_dir_state
    ):
        env = build_archive_env(tmp_path)
        if config_dir_state == "unset":
            del env["CLAUDE_CONFIG_DIR"]
        else:
            env["CLAUDE_CONFIG_DIR"] = ""

        assert_lib_env_confined(tmp_path, env, tmp_path)

    def test_script_environment_may_keep_the_real_home_while_the_key_is_not_named(self, tmp_path):
        env = {**build_archive_env(tmp_path, key_line=None), "HOME": str(tmp_path.parent)}

        assert_script_env_confined(env, tmp_path)

    def test_script_environment_needs_a_home_under_tmp_path_once_the_key_is_named(self, tmp_path):
        env = {**build_archive_env(tmp_path), "HOME": str(tmp_path.parent)}

        with pytest.raises(AssertionError, match="HOME"):
            assert_script_env_confined(env, tmp_path)

    def test_script_environment_always_needs_a_config_dir_under_tmp_path(self, tmp_path):
        env = {**build_archive_env(tmp_path, key_line=None), "CLAUDE_CONFIG_DIR": str(tmp_path.parent)}

        with pytest.raises(AssertionError, match="CLAUDE_CONFIG_DIR"):
            assert_script_env_confined(env, tmp_path)

    def test_script_environment_needs_a_config_dir_under_tmp_path_even_when_home_is_outside_it(self, tmp_path):
        env = {**build_archive_env(tmp_path, key_line=None), "HOME": str(tmp_path.parent)}
        del env["CLAUDE_CONFIG_DIR"]

        with pytest.raises(AssertionError, match="CLAUDE_CONFIG_DIR"):
            assert_script_env_confined(env, tmp_path)

    def test_script_environment_check_accepts_a_fully_confined_environment(self, tmp_path):
        env = build_archive_env(tmp_path)

        assert_script_env_confined(env, tmp_path)
        assert_lib_env_confined(tmp_path, env, tmp_path)


class TestReasonVariables:
    def test_causes_are_the_documented_thirteen_with_distinct_neutral_documented_values(self):
        reasons = reason_variables()

        assert set(reasons) == _EXPECTED_REASON_CAUSES
        values = list(reasons.values())
        assert len(set(values)) == len(values), "two causes share one reason string"
        documentation = _DOC.read_text()
        for cause, value in reasons.items():
            assert value, f"{cause} has an empty reason"
            assert "\n" not in value, f"{cause} spans lines"
            assert "agent-branch-records" not in value, f"{cause} names the root path or the sentinel"
            words = set(re.findall(r"\w+", value))
            assert not words & _COMMANDS_THAT_CREATE_THE_ROOT_OR_SENTINEL, (
                f"{cause} names a command that creates the root or sentinel"
            )
            assert f"`{value}`" in documentation, f"{cause}'s reason is missing from the docs' reason table"


class TestBranchRecordsStatus:
    def test_disabled_when_the_config_has_no_key_row(self, tmp_path, status_shell_options):
        env = build_archive_env(tmp_path, key_line=None)

        report = run_status(tmp_path, env, status_shell_options)

        assert (report.state, report.reason, report.account_dir) == ("disabled", "", "")

    def test_ready_names_the_account_directory_and_creates_nothing(self, tmp_path, status_shell_options):
        env = build_archive_env(tmp_path)
        root_before = list_tree(archive_root(env))

        report = run_status(tmp_path, env, status_shell_options)

        assert (report.state, report.reason) == ("ready", "")
        assert report.account_dir == str(account_directory(env))
        assert list_tree(archive_root(env)) == root_before

    def test_ready_under_the_default_config_dir_when_claude_config_dir_is_unset(self, tmp_path, status_shell_options):
        default_config_dir = tmp_path / "archive_home" / ".claude"
        env = build_archive_env(tmp_path, config_dir=default_config_dir)
        del env["CLAUDE_CONFIG_DIR"]

        report = run_status(tmp_path, env, status_shell_options)

        assert (report.state, report.reason) == ("ready", "")
        assert report.account_dir == str(archive_root(env) / account_component(default_config_dir))

    @pytest.mark.parametrize("sentinel_state", ["missing", "directory", "dangling-root"])
    def test_unavailable_without_a_regular_file_sentinel(self, tmp_path, reason, status_shell_options, sentinel_state):
        absent_volume_root = tmp_path / "absent_volume" / "agent-branch-records"
        if sentinel_state == "dangling-root":
            env = build_archive_env(tmp_path, root_symlink_target=absent_volume_root, create_symlink_target=False)
        else:
            env = build_archive_env(tmp_path, create_sentinel=False)
        if sentinel_state == "directory":
            (archive_root(env) / SENTINEL_NAME).mkdir()

        report = run_status(tmp_path, env, status_shell_options)

        assert (report.state, report.reason) == ("unavailable", reason["SENTINEL"])
        assert not absent_volume_root.parent.exists(), "the status call created the dangling root's target"

    @requires_non_root
    @pytest.mark.parametrize("read_only_target", ["root", "account"])
    def test_unavailable_when_the_write_target_is_read_only(
        self, tmp_path, reason, status_shell_options, read_only_target
    ):
        env = build_archive_env(tmp_path)
        account_dir = account_directory(env)
        if read_only_target == "account":
            account_dir.mkdir()
        target = archive_root(env) if read_only_target == "root" else account_dir

        with read_only(target):
            report = run_status(tmp_path, env, status_shell_options)

        expected_reason = reason["ROOT_NOT_WRITABLE"] if read_only_target == "root" else reason["ACCOUNT_NOT_WRITABLE"]
        assert (report.state, report.reason) == ("unavailable", expected_reason)

    def test_unavailable_for_a_relative_config_dir(self, tmp_path, reason, status_shell_options):
        env = {**build_archive_env(tmp_path), "CLAUDE_CONFIG_DIR": "relative/config"}

        report = run_status(tmp_path, env, status_shell_options)

        assert (report.state, report.reason) == ("unavailable", reason["CONFIG"])

    @pytest.mark.timing
    @pytest.mark.parametrize("stalled_probe", ["sentinel", "writability"])
    def test_unavailable_when_a_probe_outlives_the_cap(self, tmp_path, reason, stalled_probe):
        shim_dir = tmp_path / "stub-bin"
        shim_dir.mkdir()
        if not write_scaled_timeout_shim(shim_dir):
            pytest.skip("neither timeout(1) nor gtimeout(1) available — BSD/macOS without coreutils")
        stall = f"exec sleep {scaled_shim_sleep(10)}"
        if stalled_probe == "sentinel":
            write_shim(shim_dir, "test", stall)
        else:
            # The sentinel probe is `test -f`, which this shim answers, so only the `test -w` probe stalls.
            write_shim(shim_dir, "test", f'[ "$1" = -w ] && {stall}\nexec {shlex.quote(shutil.which("test"))} "$@"')
        env = shim_path_env(build_archive_env(tmp_path), shim_dir)

        with assert_cap_engaged(shim_dir, production_cap=5):
            report = run_status(tmp_path, env)

        assert (report.state, report.reason) == ("unavailable", reason["STALLED"])

    def test_unavailable_for_a_malformed_enabling_line_with_the_config_warning(
        self, tmp_path, reason, status_shell_options
    ):
        env = build_archive_env(tmp_path, key_line="agent_branch_records = yes")

        run = run_lib(tmp_path, env, "call_status", status_shell_options)

        assert (run.only.state, run.only.reason) == ("unavailable", reason["KEY_UNREADABLE"])
        assert "_config.sh: warning:" in run.stderr

    @pytest.mark.parametrize(
        ("key_line", "toml_scenario"),
        [
            pytest.param('agent_branch_records = "true"', None, id="quoted-true"),
            pytest.param("agent_branch_records: true", None, id="colon-instead-of-equals"),
            pytest.param("agent_branch_records = true # enabled", None, id="trailing-comment"),
            pytest.param(
                "agent_branch_records = false\nagent_branch_records = yes", None, id="malformed-line-after-a-false-line"
            ),
            pytest.param("agent_branch_records", None, id="key-name-alone"),
            pytest.param(_FALSE_LINE, "unreadable-file", marks=requires_non_root, id="unreadable-file"),
            pytest.param(_FALSE_LINE, "directory-at-the-toml-path", id="directory-at-the-toml-path"),
            pytest.param(_FALSE_LINE, "dangling-symlink-at-the-toml-path", id="dangling-symlink-at-the-toml-path"),
            pytest.param('agent_branch_records = "on"', None, id="quoted-non-boolean"),
        ],
    )
    def test_unavailable_when_the_toml_cannot_be_read_or_the_key_line_is_not_true_or_false(
        self, tmp_path, reason, status_shell_options, key_line, toml_scenario
    ):
        env = build_archive_env(tmp_path, key_line=key_line)
        toml_path = Path(env["CLAUDE_CONFIG_DIR"]) / "claude-config.toml"
        if toml_scenario == "unreadable-file":
            toml_path.chmod(0o000)
        elif toml_scenario == "directory-at-the-toml-path":
            toml_path.unlink()
            toml_path.mkdir()
        elif toml_scenario == "dangling-symlink-at-the-toml-path":
            toml_path.unlink()
            toml_path.symlink_to(tmp_path / "absent_toml_target")
        root_before = list_tree(archive_root(env))

        report = run_status(tmp_path, env, status_shell_options)

        assert (report.state, report.reason, report.account_dir) == ("unavailable", reason["KEY_UNREADABLE"], "")
        assert list_tree(archive_root(env)) == root_before

    @requires_non_root
    def test_unavailable_when_the_config_dir_cannot_be_searched(self, tmp_path, reason, status_shell_options):
        env = build_archive_env(tmp_path, key_line="agent_branch_records = true")
        config_dir = Path(env["CLAUDE_CONFIG_DIR"])
        root_before = list_tree(archive_root(env))

        config_dir.chmod(0o600)
        try:
            report = run_status(tmp_path, env, status_shell_options)
        finally:
            config_dir.chmod(0o700)

        assert (report.state, report.reason, report.account_dir) == ("unavailable", reason["KEY_UNREADABLE"], "")
        assert list_tree(archive_root(env)) == root_before

    @pytest.mark.parametrize("ancestor_kind", ["regular-file", "self-referential-symlink"])
    def test_unavailable_when_an_ancestor_of_the_config_dir_is_not_a_directory(
        self, tmp_path, reason, status_shell_options, ancestor_kind
    ):
        ancestor = tmp_path / "ancestor"
        env = build_archive_env(tmp_path, config_dir=ancestor / "config", key_line=None)
        shutil.rmtree(ancestor)
        if ancestor_kind == "regular-file":
            ancestor.write_text("")
            # Executable, so only the directory test refuses it; a search-permission test alone would pass it.
            ancestor.chmod(0o755)
        else:
            ancestor.symlink_to(ancestor)
        root_before = list_tree(archive_root(env))

        report = run_status(tmp_path, env, status_shell_options)

        assert (report.state, report.reason, report.account_dir) == ("unavailable", reason["KEY_UNREADABLE"], "")
        assert list_tree(archive_root(env)) == root_before

    def test_unavailable_without_blocking_when_a_fifo_sits_at_the_toml_path(
        self, tmp_path, reason, status_shell_options
    ):
        env = build_archive_env(tmp_path, key_line=None)
        toml_path = Path(env["CLAUDE_CONFIG_DIR"]) / "claude-config.toml"
        toml_path.unlink()
        os.mkfifo(toml_path)
        root_before = list_tree(archive_root(env))

        report = run_status(tmp_path, env, status_shell_options)

        assert (report.state, report.reason, report.account_dir) == ("unavailable", reason["KEY_UNREADABLE"], "")
        assert list_tree(archive_root(env)) == root_before

    def test_unavailable_when_reading_the_off_toml_fails(self, tmp_path, reason, status_shell_options):
        env = build_archive_env(tmp_path, key_line="agent_branch_records = false")
        # _config.sh reads the file with `$(<file)`, so only the lib's own read goes through `cat`.
        write_shim(tmp_path / "cat_shim", "cat", "exit 1")
        root_before = list_tree(archive_root(env))

        report = run_status(tmp_path, shim_path_env(env, tmp_path / "cat_shim"), status_shell_options)

        assert (report.state, report.reason, report.account_dir) == ("unavailable", reason["KEY_UNREADABLE"], "")
        assert list_tree(archive_root(env)) == root_before

    def test_ready_for_an_enabling_line_followed_by_a_malformed_duplicate(self, tmp_path, status_shell_options):
        env = build_archive_env(tmp_path, key_line="agent_branch_records = true\nagent_branch_records = yes")

        run = run_lib(tmp_path, env, "call_status", status_shell_options)

        assert (run.only.state, run.only.reason) == ("ready", "")
        assert "_config.sh: warning:" in run.stderr

    @pytest.mark.parametrize(
        ("key_line", "removed"),
        [
            pytest.param("agent_branch_records = false", None, id="false"),
            pytest.param(
                "agent_branch_records = true\nagent_branch_records = false", None, id="true-line-overridden-by-a-later-false"
            ),
            pytest.param("# note\n\nautonomous_shipping = false", None, id="no-line-for-the-key"),
            pytest.param("# agent_branch_records = yes", None, id="commented-line"),
            pytest.param("# agent_branch_records = yes\nagent_branch_records = false", None, id="commented-line-then-false"),
            pytest.param("autonomous_shipping = yes", None, id="malformed-line-for-an-unrelated-key"),
            pytest.param(None, "toml", id="no-toml-file"),
            pytest.param(None, "config-dir", id="no-config-dir"),
        ],
    )
    def test_disabled_without_a_reason_when_the_key_is_off_or_absent(
        self, tmp_path, status_shell_options, key_line, removed
    ):
        env = build_archive_env(tmp_path, key_line=key_line)
        config_dir = Path(env["CLAUDE_CONFIG_DIR"])
        if removed == "toml":
            (config_dir / "claude-config.toml").unlink()
        elif removed == "config-dir":
            shutil.rmtree(config_dir)

        report = run_status(tmp_path, env, status_shell_options)

        assert (report.state, report.reason, report.account_dir) == ("disabled", "", "")

    @pytest.mark.parametrize(
        "key_line",
        [
            pytest.param(
                'agent_branch_records = false\npr_cost_disclosure = "dollars"', id="false-row-then-another-keys-enum-row"
            ),
            pytest.param('pr_cost_disclosure = "dollars"', id="another-keys-enum-row-and-no-row-for-the-key"),
        ],
    )
    def test_disabled_when_a_well_formed_row_for_another_key_is_neither_true_nor_false(
        self, tmp_path, status_shell_options, key_line
    ):
        env = build_archive_env(tmp_path, key_line=key_line)

        report = run_status(tmp_path, env, status_shell_options)

        assert (report.state, report.reason, report.account_dir) == ("disabled", "", "")

    @pytest.mark.parametrize(
        ("key_line", "expected_state"),
        [
            pytest.param("agent_branch_records = false", "disabled", id="false"),
            pytest.param('agent_branch_records = "on"', "unavailable", id="quoted-non-boolean"),
        ],
    )
    def test_a_leading_utf8_bom_is_ignored_when_judging_the_key_line(
        self, tmp_path, reason, status_shell_options, key_line, expected_state
    ):
        env = build_archive_env(tmp_path)
        (Path(env["CLAUDE_CONFIG_DIR"]) / "claude-config.toml").write_bytes(_UTF8_BOM + key_line.encode() + b"\n")

        report = run_status(tmp_path, env, status_shell_options)

        expected_reason = reason["KEY_UNREADABLE"] if expected_state == "unavailable" else ""
        assert (report.state, report.reason, report.account_dir) == (expected_state, expected_reason, "")

    @pytest.mark.parametrize(
        ("toml_bytes", "expected_state"),
        [
            pytest.param(b"agent_branch_records = false\n\x00", "disabled", id="nul-after-the-false-line"),
            pytest.param(b"agent_\x00branch_records = banana\n", "unavailable", id="nul-inside-the-key-name"),
        ],
    )
    def test_a_null_byte_in_the_toml_is_judged_like_any_other_byte_and_leaves_stderr_without_bashs_warning(
        self, tmp_path, reason, status_shell_options, toml_bytes, expected_state
    ):
        env = build_archive_env(tmp_path)
        (Path(env["CLAUDE_CONFIG_DIR"]) / "claude-config.toml").write_bytes(toml_bytes)

        run = run_lib(tmp_path, env, "call_status", status_shell_options)

        expected_reason = reason["KEY_UNREADABLE"] if expected_state == "unavailable" else ""
        assert (run.only.state, run.only.reason, run.only.account_dir) == (expected_state, expected_reason, "")
        assert "null byte" not in run.stderr

    @pytest.mark.parametrize(
        ("toml_bytes", "expected_state"),
        [
            pytest.param(b"agent_branch_records = yes\r\n", "unavailable", id="crlf-non-boolean"),
            pytest.param(b"agent_branch_records = yes\r", "unavailable", id="lone-cr-non-boolean-without-a-newline"),
            pytest.param(b"agent_branch_records = false\r\n", "disabled", id="crlf-false"),
        ],
    )
    def test_a_carriage_return_ends_no_line_early_when_judging_the_key_line(
        self, tmp_path, reason, status_shell_options, toml_bytes, expected_state
    ):
        env = build_archive_env(tmp_path)
        (Path(env["CLAUDE_CONFIG_DIR"]) / "claude-config.toml").write_bytes(toml_bytes)

        report = run_status(tmp_path, env, status_shell_options)

        expected_reason = reason["KEY_UNREADABLE"] if expected_state == "unavailable" else ""
        assert (report.state, report.reason, report.account_dir) == (expected_state, expected_reason, "")

    def test_disabled_when_the_toml_path_is_a_symlink_to_a_regular_file_holding_the_false_line(
        self, tmp_path, status_shell_options
    ):
        env = build_archive_env(tmp_path)
        toml_path = Path(env["CLAUDE_CONFIG_DIR"]) / "claude-config.toml"
        toml_target = tmp_path / "toml_target"
        toml_target.write_text(_FALSE_LINE + "\n")
        toml_path.unlink()
        toml_path.symlink_to(toml_target)

        report = run_status(tmp_path, env, status_shell_options)

        assert (report.state, report.reason, report.account_dir) == ("disabled", "", "")

    def test_the_toml_check_refuses_a_nonexistent_relative_directory_instead_of_walking_forever(self, tmp_path):
        env = build_archive_env(tmp_path)
        # The run_lib timeout bounds a walk that never ends.
        body = (
            ': >"$CALL_STDOUT_FILE"\n'
            "walk_rc=0\n"
            "_branch_records_toml_confirms_off relative/absent_directory || walk_rc=$?\n"
            'report_call "$walk_rc"'
        )

        run = run_lib(tmp_path, env, body)

        assert run.only.rc == 1

    def test_unavailable_with_an_unset_home(self, tmp_path, reason, status_shell_options):
        env = build_archive_env(tmp_path)

        report = run_lib(tmp_path, env, "unset HOME\ncall_status", status_shell_options).only

        assert (report.state, report.reason) == ("unavailable", reason["SENTINEL"])

    @pytest.mark.parametrize(
        ("schema_state", "expected_warning"),
        [
            pytest.param("absent-file", "schema file not found or unreadable", id="status-3-absent-schema-file"),
            pytest.param(
                "readable-without-the-key-row",
                "no schema row for key in an otherwise-readable schema file",
                id="status-4-no-row-for-the-key",
            ),
        ],
    )
    def test_unavailable_when_the_schema_cannot_answer_for_the_key(
        self, tmp_path, reason, status_shell_options, schema_state, expected_warning
    ):
        env = build_archive_env(tmp_path)
        schema_path = tmp_path / "config-keys.psv"
        if schema_state == "readable-without-the-key-row":
            schema_path.write_text("autonomous_shipping|bool|false|config-dir|false|config-dir|||Autonomous shipping||\n")
        # _config.sh assigns the schema path when sourced, so only an assignment after the source takes effect.
        body = f"_CONFIG_SCHEMA_FILE={shlex.quote(str(schema_path))}\ncall_status"

        run = run_lib(tmp_path, env, body, status_shell_options)

        assert (run.only.state, run.only.reason, run.only.account_dir) == ("unavailable", reason["CONFIG"], "")
        assert expected_warning in run.stderr

    @pytest.mark.parametrize(
        ("key_line", "expected_state"),
        [
            pytest.param("agent_branch_records = false", "disabled", id="key-off"),
            pytest.param(ENABLING_LINE, "ready", id="key-on"),
        ],
    )
    def test_config_dir_with_a_space_and_a_non_ascii_character_resolves_like_any_other(
        self, tmp_path, status_shell_options, key_line, expected_state
    ):
        config_dir = tmp_path / "cfg \u00e9 dir"
        env = build_archive_env(tmp_path, config_dir=config_dir, key_line=key_line)

        report = run_status(tmp_path, env, status_shell_options)

        assert report.state == expected_state
        expected_account_dir = str(account_directory(env)) if expected_state == "ready" else ""
        assert report.account_dir == expected_account_dir
        if expected_state == "ready":
            assert Path(report.account_dir).name == account_component(config_dir)

    @pytest.mark.parametrize("removed", ["toml", "config-dir"])
    def test_config_dir_with_a_space_and_a_non_ascii_character_reads_as_disabled_when_the_toml_or_the_dir_is_absent(
        self, tmp_path, status_shell_options, removed
    ):
        config_dir = tmp_path / "cfg \u00e9 dir"
        env = build_archive_env(tmp_path, config_dir=config_dir, key_line=None)
        if removed == "toml":
            (config_dir / "claude-config.toml").unlink()
        else:
            shutil.rmtree(config_dir)

        report = run_status(tmp_path, env, status_shell_options)

        assert (report.state, report.reason, report.account_dir) == ("disabled", "", "")


class TestAccountDirectory:
    def test_config_dirs_differing_by_an_escape_and_a_slash_get_distinct_components(self, tmp_path):
        escaped_config_dir = tmp_path / "a%2Fb"
        nested_config_dir = tmp_path / "a" / "b"
        escaped_env = build_archive_env(tmp_path, config_dir=escaped_config_dir)
        nested_env = build_archive_env(tmp_path, config_dir=nested_config_dir)

        escaped_report = run_status(tmp_path, escaped_env)
        nested_report = run_status(tmp_path, nested_env)

        assert Path(escaped_report.account_dir).name == account_component(escaped_config_dir)
        assert Path(nested_report.account_dir).name == account_component(nested_config_dir)
        assert escaped_report.account_dir != nested_report.account_dir

    def test_symlinked_config_dir_encodes_its_canonical_path(self, tmp_path):
        real_config_dir = tmp_path / "real_config"
        env = build_archive_env(tmp_path, config_dir=real_config_dir)
        linked_config_dir = tmp_path / "linked_config"
        linked_config_dir.symlink_to(real_config_dir)

        report = run_status(tmp_path, {**env, "CLAUDE_CONFIG_DIR": str(linked_config_dir)})

        assert Path(report.account_dir).name == account_component(real_config_dir)

    @pytest.mark.parametrize("preexisting_mode", [None, 0o755], ids=["new", "preexisting-0755"])
    def test_account_and_created_directories_end_owner_only_under_a_permissive_caller_umask(
        self, tmp_path, archive_worktree, preexisting_mode
    ):
        env = build_archive_env(tmp_path)
        account_dir = account_directory(env)
        if preexisting_mode is not None:
            account_dir.mkdir()
            account_dir.chmod(preexisting_mode)

        report = run_archive(tmp_path, env, archive_worktree)

        assert report.rc == 0
        snapshot_dir = report.snapshot
        key_dir = snapshot_dir.parent
        # The final chmod over the snapshot hides its creating umask, which the copy-time shim test below pins.
        for directory in (account_dir, key_dir.parent, key_dir, snapshot_dir):
            assert stat.S_IMODE(directory.stat().st_mode) == 0o700, f"{directory} is not owner-only"

    def test_snapshot_directory_is_owner_only_during_the_copy_under_a_permissive_caller_umask(
        self, tmp_path, archive_worktree
    ):
        env = build_archive_env(tmp_path)
        write_tree(archive_worktree.worktree / "agent-reviews", {"finding.md": "text\n"})
        owner_only_parent = tmp_path / "owner_only_parent"
        # cp -RPp SOURCE DEST: the snapshot directory is DEST's parent.
        write_cp_shim(
            tmp_path / "cp_shim",
            'destination="${@: -1}"\n'
            f'find "${{destination%/*}}" -maxdepth 0 -perm 0700 > {shlex.quote(str(owner_only_parent))}\n'
            'exec "$REAL_CP" "$@"',
        )

        report = run_archive(tmp_path, shim_path_env(env, tmp_path / "cp_shim"), archive_worktree)

        assert report.rc == 0
        assert owner_only_parent.read_text().strip() == str(report.snapshot)

    @requires_non_root
    def test_archive_succeeds_under_a_read_only_root_once_the_account_directory_exists(
        self, tmp_path, archive_worktree
    ):
        env = build_archive_env(tmp_path)
        write_tree(archive_worktree.worktree / "agent-reviews", {"round-1.md": "first\n"})
        first_snapshot = archive_successfully(tmp_path, env, archive_worktree)
        write_tree(archive_worktree.worktree / "agent-reviews", {"round-2.md": "second\n"})

        with read_only(archive_root(env)):
            status = run_status(tmp_path, env)
            second = run_archive(tmp_path, env, archive_worktree)

        assert status.state == "ready"
        assert second.rc == 0
        assert second.snapshot != first_snapshot
        assert (second.snapshot / "manifest.json").is_file()
