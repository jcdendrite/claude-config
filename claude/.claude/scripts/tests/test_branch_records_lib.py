"""Direct tests for the readiness, reason-table and account-directory half of _branch-records-lib.sh.

The lib is sourced standalone in `bash -c`. The snapshot half is in test_branch_records_archive.py.
Neither cleanup script is run here: the input matrix for the archive lives at this seam, and the
script tests hold one case per branch. Every command starts with CDPATH set to a real directory and
umask 022, and every call's report asserts both unchanged along with cwd and an empty stdout.
"""
from __future__ import annotations

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

from ._branch_records_helpers import (
    SENTINEL_NAME,
    STATUS_SHELL_OPTION_IDS,
    STATUS_SHELL_OPTIONS,
    ArchiveWorktree,
    account_component,
    archive_root,
    archive_successfully,
    build_archive_env,
    isolate_test_process,
    list_tree,
    make_archive_worktree,
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

_DOC = REPO_ROOT / "docs" / "agent-branch-records.md"
_EXPECTED_REASON_CAUSES = {
    "CONFIG", "SENTINEL", "STALLED", "ROOT_NOT_WRITABLE", "ACCOUNT_NOT_WRITABLE", "LEDGER_KEY",
    "UNSUPPORTED_ENTRY", "DIRECTORY", "SNAPSHOT_MKDIR", "COPY_FAILED", "COPY_MISMATCH", "MANIFEST",
}
_COMMANDS_THAT_CREATE_THE_ROOT_OR_SENTINEL = {"touch", "mkdir", "ln"}


@pytest.fixture()
def reason() -> dict[str, str]:
    return reason_variables()


@pytest.fixture(autouse=True)
def _isolated_test_process(tmp_path, monkeypatch) -> None:
    isolate_test_process(tmp_path, monkeypatch)


@pytest.fixture(params=STATUS_SHELL_OPTIONS, ids=STATUS_SHELL_OPTION_IDS)
def status_shell_options(request) -> str:
    return request.param


@pytest.fixture()
def archive_worktree(tmp_path) -> ArchiveWorktree:
    return make_archive_worktree(tmp_path)


class TestProcessIsolation:
    def test_autouse_fixture_starts_each_case_in_its_own_tmp_path(self, tmp_path):
        assert Path.cwd() == tmp_path


class TestReasonVariables:
    def test_causes_are_the_documented_twelve_with_distinct_neutral_documented_values(self):
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

        expected_account_dir = archive_root(env) / account_component(Path(env["CLAUDE_CONFIG_DIR"]))
        assert (report.state, report.reason) == ("ready", "")
        assert report.account_dir == str(expected_account_dir)
        assert list_tree(archive_root(env)) == root_before

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
        account_dir = archive_root(env) / account_component(Path(env["CLAUDE_CONFIG_DIR"]))
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

    def test_disabled_for_a_malformed_enabling_line_with_the_config_warning(self, tmp_path, status_shell_options):
        env = build_archive_env(tmp_path, key_line="agent_branch_records = yes")

        run = run_lib(tmp_path, env, "call_status", status_shell_options)

        assert run.only.state == "disabled"
        assert "_config.sh: warning:" in run.stderr

    def test_unavailable_with_an_unset_home(self, tmp_path, reason, status_shell_options):
        env = build_archive_env(tmp_path)

        report = run_lib(tmp_path, env, "unset HOME\ncall_status", status_shell_options).only

        assert (report.state, report.reason) == ("unavailable", reason["SENTINEL"])


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
        account_dir = archive_root(env) / account_component(Path(env["CLAUDE_CONFIG_DIR"]))
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
