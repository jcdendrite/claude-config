"""Direct tests for branch_records_archive_worktree in _branch-records-lib.sh, sourced standalone in `bash -c`.

The readiness, reason-table and account-directory half is in test_branch_records_lib.py. Neither cleanup
script is run here: the input matrix for the archive lives at this seam, and the script tests hold one
case per branch. A case modifies a snapshot only through `archive_successfully`, which fails the case
unless the setup archive produced a snapshot inside the archive root.
"""
from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from helpers import SCRIPTS_DIR, assert_cap_engaged, ignore_agent_reviews, scaled_shim_sleep, write_scaled_timeout_shim

from . import _branch_records_helpers as records_helpers
from ._branch_records_helpers import (
    ARCHIVE_ARGUMENT_NAMES,
    SENTINEL_NAME,
    ArchiveWorktree,
    account_directory,
    archive_root,
    archive_successfully,
    archive_then_change_worktree,
    build_archive_env,
    checked_snapshot,
    expected_ledger_key,
    isolate_test_process,
    key_directory,
    list_tree,
    make_archive_worktree,
    quoted_archive_arguments,
    read_manifest,
    read_only,
    read_tree,
    requires_non_root,
    run_archive,
    run_lib,
    run_status,
    shim_path_env,
    write_cp_shim,
    write_shim,
    write_tree,
)
from .conftest import _clean_env, _commit, _git, _init_repo, _seed_session

# The fixtures live once in the helper module. An import would trip F811 on every test parameter that names one.
reason = records_helpers.reason
archive_worktree = records_helpers.archive_worktree
status_shell_options = records_helpers.status_shell_options
process_isolation = records_helpers.process_isolation

_MANIFEST_FIELDS = {
    "schema_version", "archived_at", "trigger", "branch", "tip_sha", "worktree_path",
    "main_worktree_path", "ledger_repo_root", "ledger_key", "agent_reviews",
}
_STAMP_DATE_FORMAT = "%Y%m%dT%H%M%SZ"
_MANIFEST_DATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def date_shim_body(failing_format: str | None = None, fixed_stamp: str | None = None) -> str:
    """A `date` shim that fails or prints a fixed stamp for one `+FORMAT` and runs the real binary otherwise."""
    real_date = shlex.quote(shutil.which("date"))
    if failing_format is not None:
        return f'case "$*" in *"+{failing_format}"*) exit 1;; esac\nexec {real_date} "$@"'
    return f'case "$*" in *"+{_STAMP_DATE_FORMAT}"*) echo {fixed_stamp}; exit 0;; esac\nexec {real_date} "$@"'


class TestSetupIsolation:
    def test_autouse_fixture_starts_each_case_in_its_own_tmp_path(self, tmp_path):
        assert Path.cwd() == tmp_path

    def test_exported_git_dir_does_not_redirect_worktree_setup_into_another_repository(self, tmp_path, monkeypatch):
        scratch_repository = tmp_path / "scratch_repository"
        _init_repo(scratch_repository)
        _commit(scratch_repository, "scratch")
        scratch_git_dir = scratch_repository / ".git"
        scratch_head_before = _git(scratch_repository, "rev-parse", "HEAD")
        scratch_config_before = (scratch_git_dir / "config").read_text()
        monkeypatch.setenv("GIT_DIR", str(scratch_git_dir))

        isolate_test_process(tmp_path, monkeypatch)
        make_archive_worktree(tmp_path)

        assert _git(scratch_repository, "rev-parse", "HEAD") == scratch_head_before
        assert (scratch_git_dir / "config").read_text() == scratch_config_before


class TestArchiveWorktree:
    def test_snapshot_matches_the_tree_and_manifest_records_every_field(
        self, tmp_path, archive_worktree, status_shell_options
    ):
        env = build_archive_env(tmp_path)
        files = {
            "notes/round-1/finding.md": "finding text\n",
            ".hidden": "dotfile\n",
            "name with spaces/naïve résumé.txt": "unicode name\n",
            "private.txt": "owner only\n",
        }
        source_dir = archive_worktree.worktree / "agent-reviews"
        write_tree(source_dir, files)
        (source_dir / "empty-dir").mkdir()
        (source_dir / "private.txt").chmod(0o600)

        report = run_archive(tmp_path, env, archive_worktree, trigger="cleanup-test.sh", shell_options=status_shell_options)

        assert report.rc == 0
        copied_files, copied_directories = read_tree(report.snapshot / "agent-reviews")
        assert copied_files == files
        assert copied_directories == {"notes", "notes/round-1", "name with spaces", "empty-dir"}
        for path in report.snapshot.rglob("*"):
            if path.is_file():
                assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0, f"{path} is readable beyond its owner"
        manifest = read_manifest(report.snapshot)
        repo_root = _git(archive_worktree.worktree, "rev-parse", "--show-toplevel").strip()
        assert set(manifest) == _MANIFEST_FIELDS
        datetime.strptime(manifest["archived_at"], _MANIFEST_DATE_FORMAT)
        assert manifest["schema_version"] == 1
        assert manifest["trigger"] == "cleanup-test.sh"
        assert manifest["branch"] == archive_worktree.branch
        assert manifest["tip_sha"] == _git(archive_worktree.main, "rev-parse", archive_worktree.branch).strip()
        assert manifest["worktree_path"] == str(archive_worktree.worktree)
        assert manifest["main_worktree_path"] == str(archive_worktree.main)
        assert manifest["ledger_repo_root"] == repo_root
        assert manifest["ledger_key"] == expected_ledger_key(repo_root, archive_worktree.branch)
        assert manifest["agent_reviews"] == "copied"
        assert report.snapshot.parent == key_directory(env, manifest["ledger_key"])

    def test_ledger_key_equals_the_file_name_review_ledger_append_writes(self, tmp_path, archive_worktree):
        env = build_archive_env(tmp_path)
        config_dir = Path(env["CLAUDE_CONFIG_DIR"])
        _seed_session(Path(env["HOME"]), "branch-records-session", config_dir=config_dir)
        subprocess.run(
            [
                "bash", str(SCRIPTS_DIR / "review-ledger.sh"), "append", "code-review",
                "--finding", "f", "--disposition", "ADDRESS", "--rationale", "r", "--round", "1",
                "--reviewer", "staff-sdet",
            ],
            cwd=archive_worktree.worktree, env=env, check=True, capture_output=True,
        )
        [ledger_file] = (config_dir / "review-narrative-ledger").glob("*.jsonl")

        report = run_archive(tmp_path, env, archive_worktree)

        assert report.rc == 0
        assert read_manifest(report.snapshot)["ledger_key"] == ledger_file.stem

    def test_space_and_non_ascii_in_home_worktree_path_and_branch_still_archive_with_the_ledger_key(self, tmp_path):
        # A git branch name cannot hold a space, so the branch carries only the non-ASCII characters.
        env = build_archive_env(tmp_path, home_name="hôme with space")
        target = make_archive_worktree(tmp_path, branch="feat/café-naïve", worktree_name="work tree é")
        files = {"notes/finding é.md": "text\n"}
        write_tree(target.worktree / "agent-reviews", files)

        report = run_archive(tmp_path, env, target)

        snapshot = checked_snapshot(tmp_path, env, report)
        repo_root = _git(target.worktree, "rev-parse", "--show-toplevel").strip()
        manifest = read_manifest(snapshot)
        assert read_tree(snapshot / "agent-reviews")[0] == files
        assert manifest["agent_reviews"] == "copied"
        assert manifest["worktree_path"] == str(target.worktree)
        assert manifest["branch"] == target.branch
        assert manifest["ledger_key"] == expected_ledger_key(repo_root, target.branch)
        assert snapshot.parent == key_directory(env, manifest["ledger_key"])

    def test_worktree_without_a_git_entry_hashes_its_own_path_not_the_enclosing_repository(
        self, tmp_path, archive_worktree
    ):
        env = build_archive_env(tmp_path)
        plain_worktree = archive_worktree.main / "nested_plain_worktree"
        write_tree(plain_worktree / "agent-reviews", {"finding.md": "text\n"})
        target = ArchiveWorktree(main=archive_worktree.main, worktree=plain_worktree, branch=archive_worktree.branch)
        enclosing_root = _git(plain_worktree, "rev-parse", "--show-toplevel").strip()

        report = run_archive(tmp_path, env, target)

        assert report.rc == 0
        manifest = read_manifest(report.snapshot)
        assert enclosing_root == str(archive_worktree.main), "git does not walk up from the nested directory"
        assert manifest["ledger_repo_root"] == str(plain_worktree)
        assert manifest["ledger_key"] == expected_ledger_key(str(plain_worktree), archive_worktree.branch)
        assert manifest["ledger_key"] != expected_ledger_key(enclosing_root, archive_worktree.branch)

    def test_missing_worktree_directory_archives_an_absent_snapshot(self, tmp_path, archive_worktree):
        env = build_archive_env(tmp_path)
        missing_worktree = tmp_path / "missing_worktree"
        target = replace(archive_worktree, worktree=missing_worktree)

        report = run_archive(tmp_path, env, target)

        assert report.rc == 0
        manifest = read_manifest(report.snapshot)
        assert manifest["agent_reviews"] == "absent"
        assert not (report.snapshot / "agent-reviews").exists()
        assert manifest["ledger_repo_root"] == str(missing_worktree)
        assert manifest["worktree_path"] == str(missing_worktree)
        assert manifest["ledger_key"] == expected_ledger_key(str(missing_worktree), archive_worktree.branch)

    def test_dangling_git_symlink_makes_the_worktree_path_the_ledger_root(self, tmp_path, archive_worktree):
        env = build_archive_env(tmp_path)
        plain_worktree = tmp_path / "plain_worktree"
        plain_worktree.mkdir()
        (plain_worktree / ".git").symlink_to(tmp_path / "missing_gitdir")
        target = replace(archive_worktree, worktree=plain_worktree)

        report = run_archive(tmp_path, env, target)

        assert report.rc == 0
        manifest = read_manifest(report.snapshot)
        assert manifest["ledger_repo_root"] == str(plain_worktree)
        assert manifest["agent_reviews"] == "absent"

    @requires_non_root
    def test_worktree_directory_with_read_but_no_search_permission_and_no_agent_reviews_archives_absent(
        self, tmp_path, archive_worktree
    ):
        env = build_archive_env(tmp_path)
        plain_worktree = tmp_path / "plain_worktree"
        write_tree(plain_worktree, {"unrelated.txt": "text\n"})
        target = replace(archive_worktree, worktree=plain_worktree)
        original_mode = stat.S_IMODE(plain_worktree.stat().st_mode)
        plain_worktree.chmod(0o600)
        try:
            report = run_archive(tmp_path, env, target)
        finally:
            plain_worktree.chmod(original_mode)

        assert report.rc == 0
        manifest = read_manifest(report.snapshot)
        assert manifest["agent_reviews"] == "absent"
        assert manifest["ledger_repo_root"] == str(plain_worktree)

    @requires_non_root
    @pytest.mark.parametrize("restriction", ["worktree-mode-0600", "ancestor-mode-0000"])
    def test_agent_reviews_hidden_by_permissions_archives_absent_and_worktree_remove_cannot_delete_it(
        self, tmp_path, archive_worktree, restriction
    ):
        # Recording `absent` for an entry the lib cannot stat loses data only if the remover could still delete that entry.
        env = build_archive_env(tmp_path)
        ancestor = tmp_path / "unsearchable_ancestor"
        linked = make_archive_worktree(tmp_path, "feat/hidden-entry", "unsearchable_ancestor/linked_worktree")
        ignore_agent_reviews(archive_worktree.main)
        finding = linked.worktree / "agent-reviews" / "finding.md"
        write_tree(finding.parent, {finding.name: "text\n"})
        ignore_check = subprocess.run(
            ["git", "check-ignore", "-q", "agent-reviews/finding.md"],
            cwd=linked.worktree, env=_clean_env(), capture_output=True, check=False,
        )
        assert ignore_check.returncode == 0
        control = make_archive_worktree(tmp_path, "feat/hidden-entry-control", "control_worktree")
        control_finding = control.worktree / "agent-reviews" / "finding.md"
        write_tree(control_finding.parent, {control_finding.name: "text\n"})
        control_removal = subprocess.run(
            ["git", "worktree", "remove", str(control.worktree)],
            cwd=control.main, env=_clean_env(), capture_output=True, check=False,
        )
        assert control_removal.returncode == 0
        assert not control_finding.exists()
        restricted_path, restricted_mode = (
            (linked.worktree, 0o600) if restriction == "worktree-mode-0600" else (ancestor, 0o000)
        )
        original_mode = stat.S_IMODE(restricted_path.stat().st_mode)
        restricted_path.chmod(restricted_mode)
        try:
            report = run_archive(tmp_path, env, linked)
            subprocess.run(
                ["git", "worktree", "remove", str(linked.worktree)],
                cwd=linked.main, env=_clean_env(), capture_output=True, check=False,
            )
        finally:
            if restricted_path.exists():
                restricted_path.chmod(original_mode)

        assert report.rc == 0
        assert read_manifest(report.snapshot)["agent_reviews"] == "absent"
        assert finding.read_text() == "text\n"

    def test_absent_agent_reviews_then_copied_adds_a_second_snapshot(self, tmp_path, archive_worktree):
        env = build_archive_env(tmp_path)
        first_snapshot = archive_successfully(tmp_path, env, archive_worktree)
        write_tree(archive_worktree.worktree / "agent-reviews", {"finding.md": "text\n"})

        second = run_archive(tmp_path, env, archive_worktree)

        assert second.rc == 0
        assert read_manifest(first_snapshot)["agent_reviews"] == "absent"
        assert not (first_snapshot / "agent-reviews").exists()
        assert second.snapshot != first_snapshot
        assert read_manifest(second.snapshot)["agent_reviews"] == "copied"
        assert (second.snapshot / "agent-reviews" / "finding.md").read_text() == "text\n"

    @pytest.mark.parametrize("entry_kind", ["symlink", "regular-file"])
    def test_agent_reviews_that_is_not_a_plain_directory_keeps_the_worktree(
        self, tmp_path, reason, archive_worktree, entry_kind
    ):
        env = build_archive_env(tmp_path)
        entry = archive_worktree.worktree / "agent-reviews"
        if entry_kind == "symlink":
            elsewhere = tmp_path / "elsewhere"
            elsewhere.mkdir()
            entry.symlink_to(elsewhere)
        else:
            entry.write_text("not a directory\n")
        root_before = list_tree(archive_root(env))

        report = run_archive(tmp_path, env, archive_worktree)

        assert (report.rc, report.reason, report.snapshot) == (1, reason["UNSUPPORTED_ENTRY"], None)
        assert list_tree(archive_root(env)) == root_before

    def test_nested_symlink_keeps_the_worktree_though_its_target_exists(self, tmp_path, reason, archive_worktree):
        env = build_archive_env(tmp_path)
        source_dir = archive_worktree.worktree / "agent-reviews"
        write_tree(source_dir, {"sibling.md": "target\n"})
        (source_dir / "link.md").symlink_to("sibling.md")
        root_before = list_tree(archive_root(env))

        report = run_archive(tmp_path, env, archive_worktree)

        assert (report.rc, report.reason, report.snapshot) == (1, reason["UNSUPPORTED_ENTRY"], None)
        assert list_tree(archive_root(env)) == root_before

    def test_nested_fifo_keeps_the_worktree_and_archives_nothing(self, tmp_path, reason, archive_worktree):
        env = build_archive_env(tmp_path)
        source_dir = archive_worktree.worktree / "agent-reviews"
        write_tree(source_dir, {"sibling.md": "target\n"})
        os.mkfifo(source_dir / "pipe")
        root_before = list_tree(archive_root(env))

        report = run_archive(tmp_path, env, archive_worktree)

        assert (report.rc, report.reason, report.snapshot) == (1, reason["UNSUPPORTED_ENTRY"], None)
        assert list_tree(archive_root(env)) == root_before

    @requires_non_root
    def test_unreadable_subdirectory_keeps_the_worktree_and_archives_nothing(
        self, tmp_path, reason, archive_worktree
    ):
        env = build_archive_env(tmp_path)
        unreadable_dir = archive_worktree.worktree / "agent-reviews" / "unreadable"
        write_tree(unreadable_dir.parent, {"finding.md": "text\n"})
        unreadable_dir.mkdir()
        root_before = list_tree(archive_root(env))
        unreadable_dir.chmod(0o000)
        try:
            report = run_archive(tmp_path, env, archive_worktree)
        finally:
            unreadable_dir.chmod(0o700)

        assert (report.rc, report.reason, report.snapshot) == (1, reason["UNSUPPORTED_ENTRY"], None)
        assert list_tree(archive_root(env)) == root_before

    def test_unchanged_worktree_reuses_the_first_snapshot_past_a_manifest_less_directory(
        self, tmp_path, archive_worktree
    ):
        env = build_archive_env(tmp_path)
        write_tree(archive_worktree.worktree / "agent-reviews", {"finding.md": "text\n"})
        first_snapshot = archive_successfully(tmp_path, env, archive_worktree)
        incomplete_snapshot = first_snapshot.parent / "~incomplete-sorts-last"
        incomplete_snapshot.mkdir()

        second = run_archive(tmp_path, env, archive_worktree)

        assert (second.rc, second.snapshot) == (0, first_snapshot)
        assert sorted(first_snapshot.parent.iterdir()) == sorted([first_snapshot, incomplete_snapshot])

    @pytest.mark.parametrize("change", ["changed-file", "moved-tip"])
    def test_changed_worktree_adds_a_second_snapshot(self, tmp_path, archive_worktree, change):
        env = build_archive_env(tmp_path)
        write_tree(archive_worktree.worktree / "agent-reviews", {"finding.md": "text\n"})
        first_snapshot = archive_successfully(tmp_path, env, archive_worktree)
        if change == "changed-file":
            write_tree(archive_worktree.worktree / "agent-reviews", {"finding.md": "changed\n"})
        else:
            _commit(archive_worktree.worktree, "next")

        second = run_archive(tmp_path, env, archive_worktree)

        assert second.rc == 0
        assert second.snapshot != first_snapshot
        assert (second.snapshot / "manifest.json").is_file()
        assert (first_snapshot / "manifest.json").is_file()

    def test_unchanged_worktree_reuses_the_newest_of_two_complete_snapshots(self, tmp_path, archive_worktree):
        env = build_archive_env(tmp_path)
        source_dir = archive_worktree.worktree / "agent-reviews"
        write_tree(source_dir, {"finding.md": "text\n"})
        first_snapshot = archive_successfully(tmp_path, env, archive_worktree)
        write_tree(source_dir, {"finding.md": "changed\n"})
        second_snapshot = archive_successfully(tmp_path, env, archive_worktree)
        key_dir = first_snapshot.parent
        older_first_snapshot = key_dir / "20000101T000000Z.1.1"
        first_snapshot.rename(older_first_snapshot)

        third = run_archive(tmp_path, env, archive_worktree)

        assert (third.rc, third.snapshot) == (0, second_snapshot)
        assert sorted(key_dir.iterdir()) == sorted([older_first_snapshot, second_snapshot])

    def test_absent_agent_reviews_reuses_the_absent_snapshot(self, tmp_path, archive_worktree):
        env = build_archive_env(tmp_path)
        first_snapshot = archive_successfully(tmp_path, env, archive_worktree)

        second = run_archive(tmp_path, env, archive_worktree)

        assert read_manifest(first_snapshot)["agent_reviews"] == "absent"
        assert (second.rc, second.snapshot) == (0, first_snapshot)
        assert list(first_snapshot.parent.iterdir()) == [first_snapshot]

    def test_unresolvable_branch_records_an_empty_tip_sha_and_is_reused(self, tmp_path, archive_worktree):
        env = build_archive_env(tmp_path)
        target = replace(archive_worktree, branch="feat/never-created")
        write_tree(archive_worktree.worktree / "agent-reviews", {"finding.md": "text\n"})
        first_snapshot = archive_successfully(tmp_path, env, target)

        second = run_archive(tmp_path, env, target)

        assert read_manifest(first_snapshot)["tip_sha"] == ""
        assert (second.rc, second.snapshot) == (0, first_snapshot)
        assert list(first_snapshot.parent.iterdir()) == [first_snapshot]

    def test_snapshot_stamps_are_utc_under_a_non_utc_zone(self, tmp_path, archive_worktree):
        env = {**build_archive_env(tmp_path), "TZ": "EST5EDT"}
        write_tree(archive_worktree.worktree / "agent-reviews", {"finding.md": "text\n"})

        # A stamp taken in local time under this zone offset of several hours falls outside the bracket.
        # Both stamps truncate to whole seconds, so the lower bound is truncated too.
        before_run = datetime.now(UTC).replace(microsecond=0)
        report = run_archive(tmp_path, env, archive_worktree)
        after_run = datetime.now(UTC)

        assert report.rc == 0
        archived_at = datetime.strptime(read_manifest(report.snapshot)["archived_at"], _MANIFEST_DATE_FORMAT)
        stamp = datetime.strptime(report.snapshot.name.split(".")[0], _STAMP_DATE_FORMAT)
        assert before_run <= archived_at.replace(tzinfo=UTC) <= after_run
        assert before_run <= stamp.replace(tzinfo=UTC) <= after_run

    def test_two_calls_in_one_shell_within_one_stamp_get_distinct_complete_snapshots(
        self, tmp_path, archive_worktree
    ):
        env = build_archive_env(tmp_path)
        fixed_stamp = "20260101T000000Z"
        finding = archive_worktree.worktree / "agent-reviews" / "finding.md"
        write_tree(finding.parent, {finding.name: "text\n"})
        write_shim(tmp_path / "date_shim", "date", date_shim_body(fixed_stamp=fixed_stamp))
        arguments = quoted_archive_arguments(archive_worktree)

        # One shell runs both calls, so the per-shell counter is what keeps their names apart.
        run = run_lib(
            tmp_path,
            shim_path_env(env, tmp_path / "date_shim"),
            f"call_archive {arguments}\nprintf 'changed\\n' > {shlex.quote(str(finding))}\ncall_archive {arguments}",
        )

        first_snapshot, second_snapshot = (checked_snapshot(tmp_path, env, report) for report in run.calls)
        assert first_snapshot != second_snapshot
        assert first_snapshot.name.startswith(fixed_stamp)
        assert second_snapshot.name.startswith(fixed_stamp)
        assert read_tree(first_snapshot / "agent-reviews")[0] == {"finding.md": "text\n"}
        assert read_tree(second_snapshot / "agent-reviews")[0] == {"finding.md": "changed\n"}
        assert (first_snapshot / "manifest.json").is_file()
        assert (second_snapshot / "manifest.json").is_file()

    @pytest.mark.parametrize("damage", ["deleted-file", "deleted-agent-reviews", "non-json-manifest"])
    def test_damaged_first_snapshot_is_a_reuse_miss_and_gets_a_fresh_copy(self, tmp_path, archive_worktree, damage):
        env = build_archive_env(tmp_path)
        write_tree(archive_worktree.worktree / "agent-reviews", {"finding.md": "text\n"})
        first_snapshot = archive_successfully(tmp_path, env, archive_worktree)
        if damage == "deleted-file":
            (first_snapshot / "agent-reviews" / "finding.md").unlink()
        elif damage == "deleted-agent-reviews":
            shutil.rmtree(first_snapshot / "agent-reviews")
        else:
            (first_snapshot / "manifest.json").write_text("not json\n")

        second = run_archive(tmp_path, env, archive_worktree)

        assert second.rc == 0
        assert second.snapshot != first_snapshot
        assert read_manifest(second.snapshot)["agent_reviews"] == "copied"
        assert (second.snapshot / "agent-reviews" / "finding.md").read_text() == "text\n"

    @pytest.mark.parametrize(
        ("cp_shim_body", "cause"),
        [
            pytest.param('"$REAL_CP" "$@"\nexit 1', "COPY_FAILED", id="exit-1-after-partial-copy"),
            pytest.param(
                '"$REAL_CP" "$@"\n: > "$(find "${@: -1}" -type f | head -n 1)"\nexit 0',
                "COPY_MISMATCH",
                id="exit-0-after-truncating-a-copied-file",
            ),
            # cp -RPp SOURCE DEST puts SOURCE at ${@: -2:1}; the planted link points at the fixture's finding.md.
            pytest.param(
                'ln -s finding.md "${@: -2:1}/late-link.md"\n"$REAL_CP" "$@"\nexit 0',
                "UNSUPPORTED_ENTRY",
                id="exit-0-after-a-symlink-appears-in-source-and-is-copied-as-itself",
            ),
        ],
    )
    def test_failed_copy_leaves_no_new_snapshot_and_the_earlier_one_intact(
        self, tmp_path, reason, archive_worktree, cp_shim_body, cause
    ):
        env = build_archive_env(tmp_path)
        first_snapshot = archive_then_change_worktree(tmp_path, env, archive_worktree)
        write_cp_shim(tmp_path / "cp_shim", cp_shim_body)

        report = run_archive(tmp_path, shim_path_env(env, tmp_path / "cp_shim"), archive_worktree)

        assert (report.rc, report.reason, report.snapshot) == (1, reason[cause], None)
        assert sorted(first_snapshot.parent.iterdir()) == [first_snapshot]
        assert read_tree(first_snapshot / "agent-reviews")[0] == {"finding.md": "text\n"}
        assert (first_snapshot / "manifest.json").is_file()

    def test_abandoned_copy_leaves_the_outside_targets_of_planted_links_alone(
        self, tmp_path, reason, archive_worktree
    ):
        env = build_archive_env(tmp_path)
        first_snapshot = archive_then_change_worktree(tmp_path, env, archive_worktree)
        outside_dir = tmp_path / "outside_targets"
        outside_files = {"outside-file.md": "outside file\n", "outside-subdir/kept.md": "outside directory entry\n"}
        write_tree(outside_dir, outside_files)
        # cp -RPp SOURCE DEST puts SOURCE at ${@: -2:1}; both planted links point at absolute paths outside the key directory.
        write_cp_shim(
            tmp_path / "cp_shim",
            f'ln -s {shlex.quote(str(outside_dir / "outside-file.md"))} "${{@: -2:1}}/late-file-link.md"\n'
            f'ln -s {shlex.quote(str(outside_dir / "outside-subdir"))} "${{@: -2:1}}/late-dir-link"\n'
            '"$REAL_CP" "$@"\nexit 0',
        )

        report = run_archive(tmp_path, shim_path_env(env, tmp_path / "cp_shim"), archive_worktree)

        assert (report.rc, report.reason, report.snapshot) == (1, reason["UNSUPPORTED_ENTRY"], None)
        assert read_tree(outside_dir) == (outside_files, {"outside-subdir"})
        assert sorted(first_snapshot.parent.iterdir()) == [first_snapshot]
        assert read_tree(first_snapshot / "agent-reviews")[0] == {"finding.md": "text\n"}

    @requires_non_root
    def test_read_only_key_directory_fails_the_snapshot_directory_and_leaves_the_earlier_snapshot(
        self, tmp_path, reason, archive_worktree
    ):
        env = build_archive_env(tmp_path)
        first_snapshot = archive_then_change_worktree(tmp_path, env, archive_worktree)

        with read_only(first_snapshot.parent):
            report = run_archive(tmp_path, env, archive_worktree)

        assert (report.rc, report.reason, report.snapshot) == (1, reason["SNAPSHOT_MKDIR"], None)
        assert sorted(first_snapshot.parent.iterdir()) == [first_snapshot]
        assert read_tree(first_snapshot / "agent-reviews")[0] == {"finding.md": "text\n"}

    def test_snapshot_path_collision_fails_the_snapshot_directory_and_keeps_the_colliding_directory(
        self, tmp_path, reason, archive_worktree
    ):
        env = build_archive_env(tmp_path)
        first_snapshot = archive_then_change_worktree(tmp_path, env, archive_worktree)
        # The shim plants a complete-looking directory at the snapshot path the lib builds from the key directory,
        # stamp, pid and counter, then runs the real mkdir, which fails on it.
        write_shim(
            tmp_path / "mkdir_shim",
            "mkdir",
            f'target="${{@: -1}}"\n{shlex.quote(shutil.which("mkdir"))} -p "$target"\n'
            'printf "{}" > "$target/manifest.json"\n'
            f'exec {shlex.quote(shutil.which("mkdir"))} "$@"',
        )

        report = run_archive(tmp_path, shim_path_env(env, tmp_path / "mkdir_shim"), archive_worktree)

        assert (report.rc, report.reason, report.snapshot) == (1, reason["SNAPSHOT_MKDIR"], None)
        [colliding_snapshot] = [p for p in first_snapshot.parent.iterdir() if p != first_snapshot]
        assert (colliding_snapshot / "manifest.json").read_text() == "{}"

    @pytest.mark.timing
    @pytest.mark.parametrize(
        ("stalled_tool", "stalling_arguments"),
        [("find", "-name manifest.json"), ("jq", "--arg tip_sha")],
        ids=["manifest-scan", "manifest-read"],
    )
    def test_reuse_lookup_outliving_the_cap_refuses_as_stalled_and_leaves_the_earlier_snapshot(
        self, tmp_path, reason, archive_worktree, status_shell_options, stalled_tool, stalling_arguments
    ):
        env = build_archive_env(tmp_path)
        first_snapshot = archive_then_change_worktree(tmp_path, env, archive_worktree)
        shim_dir = tmp_path / "stub-bin"
        shim_dir.mkdir()
        if not write_scaled_timeout_shim(shim_dir):
            pytest.skip("neither timeout(1) nor gtimeout(1) available — BSD/macOS without coreutils")
        # The plain-tree scan also calls find and runs uncapped, so only the reuse lookup's arguments stall.
        write_shim(
            shim_dir,
            stalled_tool,
            f'case "$*" in *"{stalling_arguments}"*) exec sleep {scaled_shim_sleep(10)};; esac\n'
            f'exec {shlex.quote(shutil.which(stalled_tool))} "$@"',
        )

        with assert_cap_engaged(shim_dir, production_cap=5):
            report = run_archive(
                tmp_path, shim_path_env(env, shim_dir), archive_worktree, shell_options=status_shell_options
            )

        assert (report.rc, report.reason, report.snapshot) == (1, reason["STALLED"], None)
        assert sorted(first_snapshot.parent.iterdir()) == [first_snapshot]
        assert (first_snapshot / "manifest.json").is_file()

    def test_key_directory_that_cannot_be_created_refuses_as_directory(self, tmp_path, reason, archive_worktree):
        env = build_archive_env(tmp_path)
        repo_root = _git(archive_worktree.worktree, "rev-parse", "--show-toplevel").strip()
        ledger_key = expected_ledger_key(repo_root, archive_worktree.branch)
        write_shim(
            tmp_path / "mkdir_shim",
            "mkdir",
            f'case "${{@: -1}}" in */branches/{ledger_key}) exit 1;; esac\nexec {shlex.quote(shutil.which("mkdir"))} "$@"',
        )

        report = run_archive(tmp_path, shim_path_env(env, tmp_path / "mkdir_shim"), archive_worktree)

        assert (report.rc, report.reason, report.snapshot) == (1, reason["DIRECTORY"], None)
        assert not key_directory(env, ledger_key).exists()

    def test_manifest_build_failure_removes_the_partial_snapshot_and_keeps_the_earlier_one(
        self, tmp_path, reason, archive_worktree
    ):
        env = build_archive_env(tmp_path)
        first_snapshot = archive_then_change_worktree(tmp_path, env, archive_worktree)
        write_shim(tmp_path / "jq_shim", "jq", f'[ "$1" = "-n" ] && exit 1\nexec {shlex.quote(shutil.which("jq"))} "$@"')

        report = run_archive(tmp_path, shim_path_env(env, tmp_path / "jq_shim"), archive_worktree)

        assert (report.rc, report.reason, report.snapshot) == (1, reason["MANIFEST"], None)
        assert sorted(first_snapshot.parent.iterdir()) == [first_snapshot]
        assert list(archive_root(env).rglob("manifest.json*")) == [first_snapshot / "manifest.json"]

    @pytest.mark.parametrize("raced_directory", ["account", "branches", "key"])
    def test_directory_made_by_a_concurrent_first_run_still_archives(
        self, tmp_path, archive_worktree, raced_directory
    ):
        env = build_archive_env(tmp_path)
        account_dir = account_directory(env)
        repo_root = _git(archive_worktree.worktree, "rev-parse", "--show-toplevel").strip()
        raced_path = {
            "account": account_dir,
            "branches": account_dir / "branches",
            "key": account_dir / "branches" / expected_ledger_key(repo_root, archive_worktree.branch),
        }[raced_directory]
        mkdir_failed_marker = tmp_path / "mkdir_failed_marker"
        real_mkdir = shlex.quote(shutil.which("mkdir"))
        write_shim(
            tmp_path / "mkdir_shim",
            "mkdir",
            f'if [ "${{@: -1}}" = {shlex.quote(str(raced_path))} ]; then\n'
            f'  {real_mkdir} "$@"\n  : > {shlex.quote(str(mkdir_failed_marker))}\n  exit 1\nfi\n'
            f'exec {real_mkdir} "$@"',
        )

        report = run_archive(tmp_path, shim_path_env(env, tmp_path / "mkdir_shim"), archive_worktree)

        assert mkdir_failed_marker.exists(), f"the {raced_directory} directory mkdir was never reached"
        assert report.rc == 0
        assert (report.snapshot / "manifest.json").is_file()

    def test_manifest_rename_failure_removes_the_partial_snapshot_and_keeps_the_earlier_one(
        self, tmp_path, reason, archive_worktree
    ):
        env = build_archive_env(tmp_path)
        first_snapshot = archive_then_change_worktree(tmp_path, env, archive_worktree)
        write_shim(tmp_path / "mv_shim", "mv", "exit 1")

        report = run_archive(tmp_path, shim_path_env(env, tmp_path / "mv_shim"), archive_worktree)

        assert (report.rc, report.reason, report.snapshot) == (1, reason["MANIFEST"], None)
        assert sorted(first_snapshot.parent.iterdir()) == [first_snapshot]
        assert list(archive_root(env).rglob("manifest.json*")) == [first_snapshot / "manifest.json"]

    @pytest.mark.parametrize(
        ("failing_format", "cause"),
        [
            pytest.param(_STAMP_DATE_FORMAT, "SNAPSHOT_MKDIR", id="first-date-call"),
            pytest.param(_MANIFEST_DATE_FORMAT, "MANIFEST", id="second-date-call"),
        ],
    )
    def test_failing_date_call_leaves_no_new_snapshot_and_the_earlier_one_intact(
        self, tmp_path, reason, archive_worktree, failing_format, cause
    ):
        env = build_archive_env(tmp_path)
        first_snapshot = archive_then_change_worktree(tmp_path, env, archive_worktree)
        write_shim(tmp_path / "date_shim", "date", date_shim_body(failing_format=failing_format))

        report = run_archive(tmp_path, shim_path_env(env, tmp_path / "date_shim"), archive_worktree)

        assert (report.rc, report.reason, report.snapshot) == (1, reason[cause], None)
        assert sorted(first_snapshot.parent.iterdir()) == [first_snapshot]
        assert read_tree(first_snapshot / "agent-reviews")[0] == {"finding.md": "text\n"}
        assert (first_snapshot / "manifest.json").is_file()

    @pytest.mark.parametrize("failure", ["empty-sha256sum", "dangling-gitdir"])
    def test_unresolvable_ledger_key_creates_no_key_directory(self, tmp_path, reason, archive_worktree, failure):
        env = build_archive_env(tmp_path)
        if failure == "empty-sha256sum":
            write_shim(tmp_path / "sha256sum_shim", "sha256sum", "exit 0")
            env = shim_path_env(env, tmp_path / "sha256sum_shim")
        else:
            (archive_worktree.worktree / ".git").write_text(f"gitdir: {tmp_path / 'missing_gitdir'}\n")

        report = run_archive(tmp_path, env, archive_worktree)

        assert (report.rc, report.reason, report.snapshot) == (1, reason["LEDGER_KEY"], None)
        assert not (account_directory(env) / "branches").exists()

    def test_sentinel_removed_between_worktrees_keeps_the_second_and_the_first_snapshot(
        self, tmp_path, reason, archive_worktree
    ):
        env = build_archive_env(tmp_path)
        second_worktree = make_archive_worktree(tmp_path, "feat/second", "second_worktree")
        first_arguments, second_arguments = (
            quoted_archive_arguments(target) for target in (archive_worktree, second_worktree)
        )
        sentinel = shlex.quote(str(archive_root(env) / SENTINEL_NAME))

        # One shell runs both calls, so a call that trusted the first call's state would still see it ready.
        run = run_lib(
            tmp_path, env, f"call_archive {first_arguments}\nrm -- {sentinel}\ncall_archive {second_arguments}"
        )

        first, second = run.calls
        assert first.rc == 0
        assert (second.rc, second.state, second.reason, second.snapshot) == (
            1, "unavailable", reason["SENTINEL"], None,
        )
        assert list(first.snapshot.parent.parent.iterdir()) == [first.snapshot.parent]
        assert (first.snapshot / "manifest.json").is_file()

    @pytest.mark.parametrize("worktree_kind", ["existing", "missing"])
    def test_disabled_key_returns_without_a_snapshot_and_leaves_the_root_unchanged(
        self, tmp_path, archive_worktree, worktree_kind
    ):
        env = build_archive_env(tmp_path, key_line=None)
        write_tree(archive_worktree.worktree / "agent-reviews", {"finding.md": "text\n"})
        target = archive_worktree
        if worktree_kind == "missing":
            target = replace(archive_worktree, worktree=tmp_path / "missing_worktree")
        root_before = list_tree(archive_root(env))

        report = run_archive(tmp_path, env, target)

        assert (report.rc, report.state, report.snapshot) == (0, "disabled", None)
        assert list_tree(archive_root(env)) == root_before

    def test_symlinked_root_reads_ready_and_holds_a_complete_snapshot(self, tmp_path, archive_worktree):
        volume_root = tmp_path / "other_volume" / "agent-branch-records"
        env = build_archive_env(tmp_path, root_symlink_target=volume_root)
        write_tree(archive_worktree.worktree / "agent-reviews", {"finding.md": "text\n"})

        status = run_status(tmp_path, env)
        report = run_archive(tmp_path, env, archive_worktree)

        assert status.state == "ready"
        assert report.rc == 0
        assert report.snapshot.is_relative_to(archive_root(env))
        assert (report.snapshot / "manifest.json").is_file()
        assert (report.snapshot / "agent-reviews" / "finding.md").read_text() == "text\n"
        assert list(volume_root.rglob("manifest.json")) != []

    @pytest.mark.parametrize("empty_argument", ARCHIVE_ARGUMENT_NAMES)
    def test_empty_argument_refuses_with_the_ledger_key_reason(
        self, tmp_path, reason, archive_worktree, empty_argument
    ):
        env = build_archive_env(tmp_path)

        report = run_lib(tmp_path, env, f"call_archive {quoted_archive_arguments(archive_worktree, empty_argument)}").only

        assert (report.rc, report.reason, report.snapshot) == (1, reason["LEDGER_KEY"], None)
        assert not (account_directory(env) / "branches").exists()

    @pytest.mark.parametrize("empty_argument", ARCHIVE_ARGUMENT_NAMES)
    def test_disabled_key_returns_zero_for_an_empty_argument_and_leaves_the_root_unchanged(
        self, tmp_path, archive_worktree, empty_argument
    ):
        env = build_archive_env(tmp_path, key_line=None)
        root_before = list_tree(archive_root(env))

        report = run_lib(tmp_path, env, f"call_archive {quoted_archive_arguments(archive_worktree, empty_argument)}").only

        assert (report.rc, report.state, report.reason, report.snapshot) == (0, "disabled", "", None)
        assert list_tree(archive_root(env)) == root_before

    def test_disabled_key_returns_zero_with_the_trigger_argument_omitted(self, tmp_path, archive_worktree):
        env = build_archive_env(tmp_path, key_line=None)
        root_before = list_tree(archive_root(env))
        three_arguments = quoted_archive_arguments(archive_worktree).rsplit(" ", 1)[0]

        report = run_lib(tmp_path, env, f"call_archive {three_arguments}").only

        assert (report.rc, report.state, report.reason, report.snapshot) == (0, "disabled", "", None)
        assert list_tree(archive_root(env)) == root_before

    @pytest.mark.parametrize("call", ["status", "archive"])
    def test_disabled_key_with_no_archive_root_reads_disabled_and_creates_nothing(
        self, tmp_path, archive_worktree, call
    ):
        env = build_archive_env(tmp_path, key_line=None, create_root=False)
        home = Path(env["HOME"])

        report = run_status(tmp_path, env) if call == "status" else run_archive(tmp_path, env, archive_worktree)

        assert (report.rc, report.state, report.reason, report.snapshot) == (0, "disabled", "", None)
        assert list_tree(home) == []

    @pytest.mark.parametrize("empty_argument", ARCHIVE_ARGUMENT_NAMES)
    def test_unavailable_archive_reports_the_status_reason_before_an_empty_argument(
        self, tmp_path, reason, archive_worktree, empty_argument
    ):
        env = build_archive_env(tmp_path, create_sentinel=False)

        report = run_lib(tmp_path, env, f"call_archive {quoted_archive_arguments(archive_worktree, empty_argument)}").only

        assert (report.rc, report.state, report.reason, report.snapshot) == (1, "unavailable", reason["SENTINEL"], None)
