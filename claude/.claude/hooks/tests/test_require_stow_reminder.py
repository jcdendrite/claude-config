"""Tests for require-stow-reminder.sh."""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    LARGE_MULTILINE_TAIL,
    REPO_ROOT,
    assert_cap_engaged,
    bash_input,
    build_path_without,
    heading_texts,
    normalize_heading,
    run_hook,
    run_hook_reason,
    scaled_shim_sleep,
    write_scaled_timeout_shim,
)

from .test_bash_floor import HOOKS_DOC_GATE_DEADLOCK_RECOVERY

STOW_REMINDER_HOOK = HOOKS_DIR / "require-stow-reminder.sh"
README_UPDATING_AFTER_GIT_PULL = "Updating after git pull"
# The example PR-body line the deny message offers. It must stay free of the
# phrases "adds new files" and "changes install.sh".
EXAMPLE_BODY_LINE = (
    "Post-merge: after git pull, re-run ./install.sh if install.sh itself was modified "
    f"or a new file is missing from your config dir (see README, {README_UPDATING_AFTER_GIT_PULL})."
)


@pytest.fixture
def stow_repo(tmp_path):
    """A claude-config-shaped repo with `main` containing one existing
    top-level entry under `claude/.claude/`, and a `feature`
    branch checked out for tests to add new content on top of.

    Tests that want a new top-level entry to be detected should add it
    on `feature` and commit there — the hook diffs `main...HEAD` from
    inside the repo's cwd."""
    repo = tmp_path / "stow-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    # Pin the settings that change how git prints or pairs paths, so ambient git config
    # cannot flip a non-ASCII or moved-file result.
    subprocess.run(["git", "config", "core.quotePath", "true"], cwd=repo, check=True)
    subprocess.run(["git", "config", "diff.renames", "true"], cwd=repo, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:jcdendrite/claude-config.git"],
        cwd=repo,
        check=True,
    )
    # Existing top-level entry on main: claude/.claude/hooks/foo.md, plus the
    # installer, so install.sh tests exercise a modification.
    # Tests can add a file inside `hooks/` or a sibling like `agents/`.
    hooks = repo / "claude" / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "foo.md").write_text("# existing hook doc\n")
    (repo / "install.sh").write_text("#!/bin/bash\necho installer\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-q", "-b", "feature"], cwd=repo, check=True)
    return repo


def commit_new_toplevel_dir(repo: Path, name: str) -> None:
    """Create `claude/.claude/<name>/file.md` and commit on the current
    branch. Used to simulate adding a brand-new top-level directory."""
    target_dir = repo / "claude" / ".claude" / name
    target_dir.mkdir(parents=True)
    (target_dir / "file.md").write_text(f"# {name}\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"add {name}"], cwd=repo, check=True)


def commit_new_toplevel_file(repo: Path, name: str) -> None:
    """Create `claude/.claude/<name>` (top-level file) and commit."""
    (repo / "claude" / ".claude" / name).write_text("data\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"add {name}"], cwd=repo, check=True)


def commit_inside_existing_toplevel(repo: Path) -> None:
    """Add a new file inside the existing `hooks/` directory. The top-level
    entry exists on main, so no reminder is raised."""
    (repo / "claude" / ".claude" / "hooks" / "bar.md").write_text("# new hook doc\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add hook doc bar"], cwd=repo, check=True)


def commit_deeply_nested_file(repo: Path) -> None:
    """Add `claude/.claude/hooks/pkg/sub/module.py`: a file two directories
    below the existing `hooks/` directory, whose top-level entry exists on main."""
    target_dir = repo / "claude" / ".claude" / "hooks" / "pkg" / "sub"
    target_dir.mkdir(parents=True)
    (target_dir / "module.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add nested module"], cwd=repo, check=True)


def commit_edit_to_existing_stowed_file(repo: Path) -> None:
    """Modify `claude/.claude/hooks/foo.md`, which exists on main."""
    (repo / "claude" / ".claude" / "hooks" / "foo.md").write_text("# edited hook doc\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "edit hook doc foo"], cwd=repo, check=True)


def commit_docs_only_change(repo: Path) -> None:
    """Add a file under `docs/`, outside every stowed package."""
    (repo / "docs").mkdir()
    (repo / "docs" / "notes.md").write_text("# notes\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add docs notes"], cwd=repo, check=True)


def commit_install_sh_change(repo: Path) -> None:
    """GH-465: change install.sh (not stowed, so a re-run reminder is
    needed independent of any new claude/.claude/ top-level entry)."""
    (repo / "install.sh").write_text("#!/bin/bash\necho updated installer\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "update install.sh"], cwd=repo, check=True)


def commit_new_toplevel_dir_and_install_sh_change(repo: Path) -> None:
    commit_new_toplevel_dir(repo, "agents")
    commit_install_sh_change(repo)


def commit_nested_add_and_new_toplevel_dir(repo: Path) -> None:
    """A nested add under `hooks/` plus a new `skills/` entry. `skills` sorts after
    `hooks`, so a read that sees only the first added path misses the new entry."""
    commit_inside_existing_toplevel(repo)
    commit_new_toplevel_dir(repo, "skills")


def commit_move_of_main_file(repo: Path, destination_dir: str) -> None:
    """`git mv` the content-identical `claude/.claude/hooks/foo.md` (seeded on main)
    into `claude/.claude/<destination_dir>/`. Under the fixture's repo-local
    `diff.renames=true` that is an `R100`, not an `A`, unless the diff passes
    `--no-renames`."""
    destination = repo / "claude" / ".claude" / destination_dir
    destination.mkdir(parents=True)
    subprocess.run(
        ["git", "mv", "claude/.claude/hooks/foo.md", f"claude/.claude/{destination_dir}/foo.md"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "commit", "-q", "-m", f"move hook doc into {destination_dir}"], cwd=repo, check=True)


def advance_main_without_resetting_feature(repo: Path, edit_main) -> None:
    """Commit `edit_main(repo)`'s change on `main` after `feature` was cut, then return
    to `feature` without moving it, so `main`'s tip and the merge-base differ."""
    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)
    edit_main(repo)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "advance main after the cut"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-q", "feature"], cwd=repo, check=True)


def seed_path_on_main(repo: Path, relative_dir: str) -> None:
    """Commit `claude/.claude/<relative_dir>/seed.md` on `main`, then reset the
    still-empty `feature` branch onto it."""
    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)
    seeded_dir = repo / "claude" / ".claude" / relative_dir
    seeded_dir.mkdir(parents=True)
    (seeded_dir / "seed.md").write_text("seed\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"seed {relative_dir}"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-q", "feature"], cwd=repo, check=True)
    subprocess.run(["git", "reset", "-q", "--hard", "main"], cwd=repo, check=True)


def assert_live_deny_control(repo: Path) -> None:
    """Add a new top-level entry on top of the fixture's commits and assert the
    hook denies it. `run_hook` reads empty stdout from any non-2 exit as allow,
    so a crashed hook would otherwise pass an allow test."""
    commit_new_toplevel_dir(repo, "control-entry")
    cmd = "gh pr create --title T --body 'no marker here'"
    reason = run_hook_reason(STOW_REMINDER_HOOK, bash_input(cmd), cwd=repo)
    assert reason is not None, "control: hook did not deny a new top-level entry"
    assert "adds new files" in reason
    assert "control-entry" in reason


# One commit shape per way the gate can trip.
TRIGGER_SHAPES = [
    pytest.param(lambda repo: commit_new_toplevel_dir(repo, "agents"), id="new-toplevel-entry"),
    pytest.param(commit_install_sh_change, id="install-sh-only"),
    pytest.param(commit_new_toplevel_dir_and_install_sh_change, id="new-toplevel-and-install-sh"),
    pytest.param(commit_nested_add_and_new_toplevel_dir, id="new-toplevel-and-nested-add"),
]


class TestRequireStowReminder:
    def test_non_pr_command_allowed(self, stow_repo):
        commit_new_toplevel_dir(stow_repo, "agents")
        assert run_hook(STOW_REMINDER_HOOK, bash_input("git status"), cwd=stow_repo) == "allow"

    def test_unrelated_remote_repo_allowed(self, tmp_path):
        """The gate is scoped to claude-config. Other repos may legitimately
        add top-level directories without any stow workflow."""
        repo = tmp_path / "other-repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "git@github.com:someone/other-app.git"],
            cwd=repo,
            check=True,
        )
        (repo / "README.md").write_text("x")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        subprocess.run(["git", "checkout", "-q", "-b", "feature"], cwd=repo, check=True)
        (repo / "claude").mkdir()
        (repo / "claude" / ".claude").mkdir()
        (repo / "claude" / ".claude" / "agents").mkdir()
        (repo / "claude" / ".claude" / "agents" / "foo.md").write_text("x")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "add agents"], cwd=repo, check=True)
        cmd = "gh pr create --title T --body 'no marker here'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=repo) == "allow"

    @pytest.mark.parametrize(
        "commit_change",
        [
            pytest.param(commit_inside_existing_toplevel, id="one-level-inside-existing-toplevel"),
            pytest.param(commit_deeply_nested_file, id="two-directories-below-existing-toplevel"),
        ],
    )
    def test_new_file_inside_existing_toplevel_without_marker_allowed(self, stow_repo, commit_change):
        """A new file under a top-level entry that exists on main raises no
        reminder."""
        commit_change(stow_repo)
        cmd = "gh pr create --title T --body 'no marker here'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "allow"
        assert_live_deny_control(stow_repo)

    def test_new_toplevel_dir_without_marker_denied(self, stow_repo):
        commit_new_toplevel_dir(stow_repo, "agents")
        cmd = "gh pr create --title 'Add agents' --body 'Adds reviewer agents.'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "deny"

    def test_new_toplevel_file_without_marker_denied(self, stow_repo):
        """A new file directly under claude/.claude/ also trips the gate —
        it is a new top-level entry."""
        commit_new_toplevel_file(stow_repo, "newfile.md")
        cmd = "gh pr create --title T --body 'add file'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "deny"

    @pytest.mark.parametrize(
        "commit_change",
        [
            pytest.param(commit_edit_to_existing_stowed_file, id="edit-to-existing-stowed-file"),
            pytest.param(commit_docs_only_change, id="docs-only-change"),
        ],
    )
    def test_change_that_adds_no_stowed_path_without_marker_allowed(self, stow_repo, commit_change):
        """An edit to a file already on main, or an added file under `docs/`
        outside every stowed package, needs no PR-body reminder."""
        commit_change(stow_repo)
        cmd = "gh pr create --title T --body 'no marker here'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "allow"
        assert_live_deny_control(stow_repo)

    # ------------------------------------------------------------------ #
    # Trigger: only a top-level entry that is new relative to main        #
    # ------------------------------------------------------------------ #

    def test_nested_add_beside_new_toplevel_entry_denied_naming_only_the_new_entry(self, stow_repo):
        commit_nested_add_and_new_toplevel_dir(stow_repo)
        cmd = "gh pr create --title T --body 'no marker here'"
        reason = run_hook_reason(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo)
        assert reason is not None
        assert "`claude/.claude/skills`" in reason
        assert "claude/.claude/hooks" not in reason

    def test_two_new_toplevel_entries_denied_naming_each_entry_once(self, stow_repo):
        commit_new_toplevel_dir(stow_repo, "agents")
        (stow_repo / "claude" / ".claude" / "agents" / "second.md").write_text("# second\n")
        subprocess.run(["git", "add", "."], cwd=stow_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "add second agents file"], cwd=stow_repo, check=True)
        commit_new_toplevel_dir(stow_repo, "rules")
        cmd = "gh pr create --title T --body 'no marker here'"
        reason = run_hook_reason(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo)
        assert reason is not None
        assert reason.count("claude/.claude/agents") == 1
        assert reason.count("claude/.claude/rules") == 1

    def test_install_sh_change_beside_nested_add_denied_with_install_sh_only_reason(self, stow_repo):
        commit_inside_existing_toplevel(stow_repo)
        commit_install_sh_change(stow_repo)
        cmd = "gh pr create --title T --body 'no marker here'"
        reason = run_hook_reason(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo)
        assert reason is not None
        assert "changes install.sh" in reason
        assert "adds new files" not in reason

    def test_non_ascii_new_toplevel_entry_denied(self, stow_repo):
        commit_new_toplevel_dir(stow_repo, "日本語")
        cmd = "gh pr create --title T --body 'no marker here'"
        reason = run_hook_reason(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo)
        assert reason is not None
        assert "adds new files" in reason
        assert "claude/.claude/日本語" in reason

    def test_file_added_inside_non_ascii_toplevel_entry_on_main_allowed(self, stow_repo):
        seed_path_on_main(stow_repo, "日本語")
        (stow_repo / "claude" / ".claude" / "日本語" / "added.md").write_text("added\n")
        subprocess.run(["git", "add", "."], cwd=stow_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "add file in non-ASCII entry"], cwd=stow_repo, check=True)
        cmd = "gh pr create --title T --body 'no marker here'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "allow"
        assert_live_deny_control(stow_repo)

    def test_file_moved_into_new_toplevel_entry_denied_naming_that_entry(self, stow_repo):
        """A move is an add for the gate, so it must not hide behind rename detection."""
        commit_move_of_main_file(stow_repo, "agents")
        cmd = "gh pr create --title T --body 'no marker here'"
        reason = run_hook_reason(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo)
        assert reason is not None
        assert "adds new files" in reason
        assert "`claude/.claude/agents`" in reason

    def test_file_moved_into_existing_toplevel_entry_allowed(self, stow_repo):
        commit_move_of_main_file(stow_repo, "hooks/moved")
        cmd = "gh pr create --title T --body 'no marker here'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "allow"
        assert_live_deny_control(stow_repo)

    def test_toplevel_entry_added_to_main_after_the_cut_allowed(self, stow_repo):
        """The probe reads the `main` tip, so an entry another PR landed since the cut
        carries the re-run and the branch's file under it needs no reminder."""

        def add_late_entry(repo: Path) -> None:
            late_dir = repo / "claude" / ".claude" / "late-entry"
            late_dir.mkdir(parents=True)
            (late_dir / "seed.md").write_text("seed\n")

        advance_main_without_resetting_feature(stow_repo, add_late_entry)
        (stow_repo / "claude" / ".claude" / "late-entry").mkdir(parents=True)
        (stow_repo / "claude" / ".claude" / "late-entry" / "added.md").write_text("added\n")
        subprocess.run(["git", "add", "."], cwd=stow_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "add file under late-entry"], cwd=stow_repo, check=True)
        merge_base = subprocess.run(
            ["git", "merge-base", "main", "HEAD"], cwd=stow_repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        probe_at_merge_base = subprocess.run(
            ["git", "cat-file", "-e", f"{merge_base}:claude/.claude/late-entry"], cwd=stow_repo, check=False
        )
        assert probe_at_merge_base.returncode != 0, "fixture: the entry must be absent at the merge-base"
        cmd = "gh pr create --title T --body 'no marker here'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "allow"
        assert_live_deny_control(stow_repo)

    def test_toplevel_entry_deleted_on_main_after_the_cut_allowed(self, stow_repo):
        """The diff runs from the merge-base, so an entry `main` deleted since the cut
        is not listed as added the way a two-dot diff against the `main` tip would."""

        def delete_hooks_entry(repo: Path) -> None:
            subprocess.run(["git", "rm", "-q", "-r", "claude/.claude/hooks"], cwd=repo, check=True)

        advance_main_without_resetting_feature(stow_repo, delete_hooks_entry)
        commit_docs_only_change(stow_repo)
        cmd = "gh pr create --title T --body 'no marker here'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "allow"
        assert_live_deny_control(stow_repo)

    def test_new_toplevel_entry_denied_when_hook_runs_from_a_repo_subdirectory(self, stow_repo):
        """The `claude/.claude/` pathspec is cwd-relative, so the diff must run from the
        repo root: a session whose cwd is below the root still sees the new entry."""
        commit_new_toplevel_dir(stow_repo, "agents")
        cmd = "gh pr create --title T --body 'no marker here'"
        subdirectory = stow_repo / "claude" / ".claude" / "hooks"
        reason = run_hook_reason(STOW_REMINDER_HOOK, bash_input(cmd), cwd=subdirectory)
        assert reason is not None
        assert "adds new files" in reason
        assert "`claude/.claude/agents`" in reason

    def test_install_sh_change_denied_when_hook_runs_from_a_repo_subdirectory(self, stow_repo):
        """The `install.sh` pathspec is cwd-relative, so the diff must run from the repo
        root: a session whose cwd is below the root still sees the installer change."""
        commit_install_sh_change(stow_repo)
        cmd = "gh pr create --title T --body 'no marker here'"
        subdirectory = stow_repo / "claude" / ".claude" / "hooks"
        reason = run_hook_reason(STOW_REMINDER_HOOK, bash_input(cmd), cwd=subdirectory)
        assert reason is not None
        assert "changes install.sh" in reason

    def test_failed_cat_file_probe_counts_added_paths_as_new_entries(self, stow_repo, tmp_path):
        """`main` resolves but the stubbed `git cat-file` exits 124, a cap-kill status git never
        returns for a missing path.
        A probe that fails cannot confirm any entry exists on `main`.
        So every added path's entry counts as new.
        That includes the nested add under `hooks/` that a working probe would allow."""
        real_git = shutil.which("git")
        assert real_git
        stub_dir = tmp_path / "stub-bin-git"
        stub_dir.mkdir()
        fake_git = stub_dir / "git"
        fake_git.write_text(
            "#!/bin/bash\n"
            'if [ "${1:-}" = cat-file ]; then\n'
            "  exit 124\n"
            "fi\n"
            f'exec {shlex.quote(real_git)} "$@"\n'
        )
        fake_git.chmod(0o755)
        commit_inside_existing_toplevel(stow_repo)
        commit_new_toplevel_dir(stow_repo, "agents")
        cmd = "gh pr create --title T --body 'no marker here'"
        reason = run_hook_reason(
            STOW_REMINDER_HOOK,
            bash_input(cmd),
            cwd=stow_repo,
            extra_env={"PATH": f"{stub_dir}:{os.environ['PATH']}"},
        )
        assert reason is not None
        assert "adds new files" in reason
        assert "`claude/.claude/agents`" in reason
        assert "`claude/.claude/hooks`" in reason

    # ------------------------------------------------------------------ #
    # GH-465: install.sh changes also need the re-run reminder            #
    # ------------------------------------------------------------------ #

    def test_install_sh_change_without_new_toplevel_denied(self, stow_repo):
        """install.sh itself is not stowed — a change to it needs the
        reminder even with no new claude/.claude/ top-level entry."""
        commit_install_sh_change(stow_repo)
        cmd = "gh pr create --title T --body 'hardens the installer'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "deny"

    def test_install_sh_change_with_marker_allowed(self, stow_repo):
        commit_install_sh_change(stow_repo)
        cmd = "gh pr create --title T --body 'post-merge: run ./install.sh'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "allow"

    def test_new_toplevel_and_install_sh_both_changed_with_marker_allowed(self, stow_repo):
        """Both triggers firing at once must not require two separate
        markers — one install.sh/stow mention in the body still covers
        both reasons."""
        commit_new_toplevel_dir(stow_repo, "agents")
        commit_install_sh_change(stow_repo)
        cmd = "gh pr create --title T --body 'post-merge: run ./install.sh'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "allow"

    def test_install_sh_only_reason_names_install_sh_not_new_files(self, stow_repo):
        """The install-only branch's REASON_DETAIL must not mention
        "adds new files" -- that wording belongs to a trigger that
        didn't fire."""
        commit_install_sh_change(stow_repo)
        cmd = "gh pr create --title T --body 'hardens the installer'"
        reason = run_hook_reason(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo)
        assert reason is not None
        assert "changes install.sh" in reason
        assert "adds new files" not in reason

    def test_new_toplevel_only_reason_names_new_files_not_install_sh(self, stow_repo):
        """The new-file-only branch's REASON_DETAIL must not claim
        install.sh changed."""
        commit_new_toplevel_dir(stow_repo, "agents")
        cmd = "gh pr create --title 'Add agents' --body 'Adds reviewer agents.'"
        reason = run_hook_reason(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo)
        assert reason is not None
        assert "adds new files" in reason
        assert "changes install.sh" not in reason

    def test_combined_trigger_reason_names_both(self, stow_repo):
        """When both triggers fire in the same PR, the reason text must
        name both -- not silently pick one and drop the other."""
        commit_new_toplevel_dir(stow_repo, "agents")
        commit_install_sh_change(stow_repo)
        cmd = "gh pr create --title T --body 'no marker here'"
        reason = run_hook_reason(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo)
        assert reason is not None
        assert "adds new files" in reason
        assert "changes install.sh" in reason

    def test_marker_install_sh_in_body_allowed(self, stow_repo):
        commit_new_toplevel_dir(stow_repo, "agents")
        cmd = "gh pr create --title T --body 'After merging, run ./install.sh'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "allow"

    def test_marker_stow_in_body_allowed(self, stow_repo):
        commit_new_toplevel_dir(stow_repo, "agents")
        cmd = "gh pr create --title T --body 'remember to re-stow'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "allow"

    def test_marker_case_insensitive_allowed(self, stow_repo):
        commit_new_toplevel_dir(stow_repo, "agents")
        cmd = "gh pr create --title T --body 'Run INSTALL.SH after merge'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "allow"

    def test_body_file_with_marker_allowed(self, stow_repo, tmp_path):
        commit_new_toplevel_dir(stow_repo, "agents")
        body = tmp_path / "body.md"
        body.write_text("Adds agents/.\n\nPost-merge: run ./install.sh.\n")
        cmd = f"gh pr create --title T --body-file {body}"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "allow"

    def test_body_file_without_marker_denied(self, stow_repo, tmp_path):
        commit_new_toplevel_dir(stow_repo, "agents")
        body = tmp_path / "body.md"
        body.write_text("Adds agents/. No reminder here.\n")
        cmd = f"gh pr create --title T --body-file {body}"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "deny"

    def test_body_file_device_file_is_denied_not_hung(self, stow_repo, tmp_path):
        """`--body-file /dev/zero` must deny without `cat` ever touching it:
        /dev/zero passes the `[ -r ]` readability check but is not a regular
        file, so the `[ ! -f ]` guard must reject it before the capped read.
        A `cat` shim records any invocation on the device (and never reads
        it), so removing the guard fails the test rather than being masked by
        the cat-timeout skip or hanging on a host without timeout(1)."""
        commit_new_toplevel_dir(stow_repo, "agents")
        cmd = "gh pr create --title T --body-file /dev/zero"

        real_cat = shutil.which("cat")
        if not real_cat:
            pytest.skip("cat not found in PATH")

        stub_dir = tmp_path / "stub-bin-cat"
        stub_dir.mkdir()
        invocation_log = tmp_path / "cat-on-device.log"
        fake_cat = stub_dir / "cat"
        fake_cat.write_text(
            "#!/bin/bash\n"
            'if [ "$1" = /dev/zero ]; then\n'
            f"  echo invoked >> {shlex.quote(str(invocation_log))}\n"
            "  exit 0\n"
            "fi\n"
            f'exec {real_cat} "$@"\n'
        )
        fake_cat.chmod(0o755)

        result = subprocess.run(
            [str(STOW_REMINDER_HOOK)],
            input=json.dumps(bash_input(cmd)),
            capture_output=True,
            text=True,
            cwd=stow_repo,
            env={**os.environ, "PATH": f"{stub_dir}:{os.environ['PATH']}"},
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert not invocation_log.exists(), "cat was invoked on the non-regular body source"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"

    @pytest.mark.timing
    def test_body_file_cat_timeout_skips_source_not_denied(self, stow_repo, tmp_path):
        """A body-source `cat` killed by the cap (exit 124) is skipped like an
        unreadable file: its partial output must not reach the scan target.
        The shim prints a marker to stdout before sleeping past the cap, and
        no marker appears anywhere else in the command. Skipping discards the
        partial output, so the gate denies; a swallowed 124 appends it and the
        gate wrongly allows."""
        commit_new_toplevel_dir(stow_repo, "agents")
        body = tmp_path / "body.md"
        body.write_text("no marker in this file\n")
        cmd = f"gh pr create --title T --body 'no reminder here' --body-file {body}"

        real_cat = shutil.which("cat")
        if not real_cat:
            pytest.skip("cat not found in PATH")
        if not shutil.which("timeout") and not shutil.which("gtimeout"):
            pytest.skip("neither timeout(1) nor gtimeout(1) available — BSD/macOS without coreutils")

        stub_dir = tmp_path / "stub-bin-cat"
        stub_dir.mkdir()
        sleep_seconds = scaled_shim_sleep(5) if write_scaled_timeout_shim(stub_dir) else 5
        fake_cat = stub_dir / "cat"
        fake_cat.write_text(
            "#!/bin/bash\n"
            f'if [ "$1" = {shlex.quote(str(body))} ]; then\n'
            "  echo 'partial output: run ./install.sh'\n"
            f"  sleep {sleep_seconds}\n"
            "fi\n"
            f'exec {real_cat} "$@"\n'
        )
        fake_cat.chmod(0o755)

        env = {"PATH": f"{stub_dir}:{os.environ['PATH']}"}
        with assert_cap_engaged(stub_dir, production_cap=5, command="cat"):
            decision = run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo, extra_env=env)
        assert decision == "deny"

    @pytest.mark.timing
    @pytest.mark.parametrize(
        ("killed_git_subcommand", "commit_change", "expected_decision"),
        [
            pytest.param("cat-file", commit_inside_existing_toplevel, "deny", id="cat-file-probe-killed-counts-as-new"),
            pytest.param(
                "diff",
                lambda repo: commit_new_toplevel_dir(repo, "agents"),
                "allow",
                id="added-path-diff-killed-fails-open",
            ),
        ],
    )
    def test_git_call_cap_kill_resolves_per_header(
        self, stow_repo, tmp_path, killed_git_subcommand, commit_change, expected_decision
    ):
        """A capped `git` call that the cap kills (exit 124) resolves per the header.
        A killed `cat-file` probe counts its top-level entry as new, although `main` has it, so the gate denies.
        A killed added-path `diff` yields no paths, although a new entry exists, so the gate fails open and allows.
        The shim sleeps past the cap only on the targeted call, then runs the real git."""
        commit_change(stow_repo)
        cmd = "gh pr create --title T --body 'no marker here'"

        real_git = shutil.which("git")
        if not real_git:
            pytest.skip("git not found in PATH")
        if not shutil.which("timeout") and not shutil.which("gtimeout"):
            pytest.skip("neither timeout(1) nor gtimeout(1) available — BSD/macOS without coreutils")

        stub_dir = tmp_path / "stub-bin-git"
        stub_dir.mkdir()
        sleep_seconds = scaled_shim_sleep(5) if write_scaled_timeout_shim(stub_dir) else 5
        # The install.sh probe also runs `git diff`, so the diff arm matches on the added-path filter.
        killed_call_match = {
            "cat-file": '[ "$1" = cat-file ]',
            "diff": '[ "$1" = diff ] && [[ " $* " == *" --diff-filter=A "* ]]',
        }[killed_git_subcommand]
        fake_git = stub_dir / "git"
        fake_git.write_text(
            "#!/bin/bash\n"
            f"if {killed_call_match}; then\n"
            f"  sleep {sleep_seconds}\n"
            "fi\n"
            f'exec {shlex.quote(real_git)} "$@"\n'
        )
        fake_git.chmod(0o755)

        env = {"PATH": f"{stub_dir}:{os.environ['PATH']}"}
        with assert_cap_engaged(stub_dir, production_cap=5, command="git"):
            decision = run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo, extra_env=env)
        assert decision == expected_decision

    @pytest.mark.timing
    def test_killed_added_path_diff_counts_only_the_complete_paths_it_printed(self, stow_repo, tmp_path):
        """The shim prints one complete NUL-terminated added path, then a truncated path with no NUL.
        The shim then sleeps past the cap.
        The cap kills the added-path `git diff` mid-output.
        The hook counts the complete path's top-level entry and denies naming it.
        The hook does not count the truncated path, so a read that kept the partial tail would name it.
        Neither entry exists on `main`, so a probe of the truncated name would count it as new."""
        commit_new_toplevel_dir(stow_repo, "agents")
        for entry_name in ("agents", "rule"):
            probe_on_main = subprocess.run(
                ["git", "cat-file", "-e", f"main:claude/.claude/{entry_name}"], cwd=stow_repo, capture_output=True, check=False
            )
            assert probe_on_main.returncode != 0, f"fixture: {entry_name} must be absent from main"
        cmd = "gh pr create --title T --body 'no marker here'"

        real_git = shutil.which("git")
        if not real_git:
            pytest.skip("git not found in PATH")
        if not shutil.which("timeout") and not shutil.which("gtimeout"):
            pytest.skip("neither timeout(1) nor gtimeout(1) available — BSD/macOS without coreutils")

        stub_dir = tmp_path / "stub-bin-git"
        stub_dir.mkdir()
        sleep_seconds = scaled_shim_sleep(5) if write_scaled_timeout_shim(stub_dir) else 5
        # The install.sh probe also runs `git diff`, so the shim matches on the added-path filter.
        fake_git = stub_dir / "git"
        fake_git.write_text(
            "#!/bin/bash\n"
            'if [ "$1" = diff ] && [[ " $* " == *" --diff-filter=A "* ]]; then\n'
            "  printf 'claude/.claude/agents/file.md\\0'\n"
            "  printf 'claude/.claude/rule'\n"
            f"  sleep {sleep_seconds}\n"
            "fi\n"
            f'exec {shlex.quote(real_git)} "$@"\n'
        )
        fake_git.chmod(0o755)

        env = {"PATH": f"{stub_dir}:{os.environ['PATH']}"}
        with assert_cap_engaged(stub_dir, production_cap=5, command="git"):
            reason = run_hook_reason(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo, extra_env=env)
        assert reason is not None
        assert "adds new files" in reason
        assert "`claude/.claude/agents`" in reason
        assert "claude/.claude/rule" not in reason

    def test_body_file_pseudo_path_is_not_read(self, stow_repo, tmp_path):
        """A `/dev/fd/N` --body-file is skipped, not read: the marker in the
        file behind an fd the hook inherits must not satisfy the gate."""
        commit_new_toplevel_dir(stow_repo, "agents")
        body = tmp_path / "body.md"
        body.write_text("Adds agents/.\n\nPost-merge: run ./install.sh.\n")
        with body.open() as body_handle:
            fd = body_handle.fileno()
            cmd = f"gh pr create --title T --body-file /dev/fd/{fd}"
            result = subprocess.run(
                [str(STOW_REMINDER_HOOK)],
                input=json.dumps(bash_input(cmd)),
                capture_output=True,
                text=True,
                cwd=stow_repo,
                pass_fds=(fd,),
                check=False,
            )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip(), "hook allowed: the pseudo-file body source was read"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        # The stow-marker deny, not an unrelated fail-closed deny.
        assert "adds new files" in payload["hookSpecificOutput"]["permissionDecisionReason"]

    def test_fill_with_marker_in_commit_message_allowed(self, stow_repo):
        """`gh pr create --fill` sources body from commits — a marker
        in any commit message on the branch satisfies the gate."""
        # Commit the new top-level with a message that mentions install.sh.
        target = stow_repo / "claude" / ".claude" / "agents"
        target.mkdir(parents=True)
        (target / "foo.md").write_text("x")
        subprocess.run(["git", "add", "."], cwd=stow_repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "add agents (post-merge: run install.sh)"],
            cwd=stow_repo,
            check=True,
        )
        cmd = "gh pr create --fill"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "allow"

    def test_fill_without_marker_denied(self, stow_repo):
        commit_new_toplevel_dir(stow_repo, "agents")  # commit msg: "add agents"
        cmd = "gh pr create --fill"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "deny"

    def test_pr_edit_label_only_allowed(self, stow_repo):
        """gh pr edit without any body-modifying flag must not be gated.
        The create-time check already enforced the marker initially; a
        non-body edit can't remove it."""
        commit_new_toplevel_dir(stow_repo, "agents")
        cmd = "gh pr edit 42 --add-label needs-review"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "allow"

    def test_pr_edit_body_without_marker_denied(self, stow_repo):
        commit_new_toplevel_dir(stow_repo, "agents")
        cmd = "gh pr edit 42 --body 'rewritten body, no marker'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "deny"

    def test_pr_edit_body_with_marker_allowed(self, stow_repo):
        commit_new_toplevel_dir(stow_repo, "agents")
        cmd = "gh pr edit 42 --body 'updated: post-merge run ./install.sh'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "allow"

    def test_pr_edit_large_multiline_body_without_marker_denied(self, stow_repo):
        """The `--body` flag check must see `--body` on the first line of a large
        multi-line command."""
        commit_new_toplevel_dir(stow_repo, "agents")
        cmd = "gh pr edit 42 --body 'rewritten body, no marker'" + LARGE_MULTILINE_TAIL
        reason = run_hook_reason(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo)
        assert reason is not None
        assert "adds new files" in reason

    @pytest.mark.parametrize(
        ("commit_message", "expect_allow"),
        [
            pytest.param("add agents (post-merge: run install.sh)", True, id="marker-in-commit-message"),
            pytest.param("add agents", False, id="control-no-marker"),
        ],
    )
    def test_fill_on_first_line_of_large_multiline_command(self, stow_repo, commit_message, expect_allow):
        """The `--fill` flag check must see `--fill` on the first line of a large
        multi-line command, so commit messages stay in the marker scan."""
        target = stow_repo / "claude" / ".claude" / "agents"
        target.mkdir(parents=True)
        (target / "foo.md").write_text("x")
        subprocess.run(["git", "add", "."], cwd=stow_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", commit_message], cwd=stow_repo, check=True)
        cmd = "gh pr create --fill" + LARGE_MULTILINE_TAIL
        reason = run_hook_reason(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo)
        if expect_allow:
            assert reason is None, reason
            # run_hook_reason reads an exit-2 hard block (empty stdout) as None.
            assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "allow"
        else:
            assert reason is not None
            assert "adds new files" in reason

    @pytest.mark.parametrize(
        ("first_line", "expect_allow"),
        [
            pytest.param("Post-merge: run ./install.sh.", True, id="marker-on-first-line"),
            pytest.param("Adds the agents directory.", False, id="control-no-marker"),
        ],
    )
    def test_large_multiline_body_file_with_marker_on_first_line(
        self, stow_repo, tmp_path, first_line, expect_allow
    ):
        """The marker scan must see `install.sh` on the first line of a large
        multi-line body file."""
        commit_new_toplevel_dir(stow_repo, "agents")
        body = tmp_path / "body.md"
        body.write_text(first_line + LARGE_MULTILINE_TAIL)
        cmd = f"gh pr create --title T --body-file {body}"
        reason = run_hook_reason(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo)
        if expect_allow:
            assert reason is None, reason
            # run_hook_reason reads an exit-2 hard block (empty stdout) as None.
            assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "allow"
        else:
            assert reason is not None
            assert "adds new files" in reason

    def test_no_main_ref_fails_open(self, tmp_path):
        """Fresh-clone state without a local `main` ref: hook must not
        block PR creation. Documented as a known fail-open in the
        header."""
        repo = tmp_path / "no-main"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "trunk"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "git@github.com:jcdendrite/claude-config.git"],
            cwd=repo,
            check=True,
        )
        (repo / "README.md").write_text("x")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        cmd = "gh pr create --title T --body 'no marker'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=repo) == "allow"

    def test_sed_absent_from_path_still_allows(self, stow_repo, tmp_path):
        """Mirror-image of the checked hooks' status-2 deny tests: this
        hook is deliberately, correctly unchecked (see its header's fail-
        open posture), so an otherwise-denyable command (new top-level
        entry, no marker) under a sed-absent PATH must still ALLOW -- the
        gh pr create/edit matcher can't determine a match, treats that the
        same as "no match", and the whole gate never engages. Pins the
        accepted fail-open posture as an executable invariant rather than
        leaving it implicit."""
        commit_new_toplevel_dir(stow_repo, "agents")
        farm_dir = tmp_path / "path-without-sed"
        farm_dir.mkdir()
        restricted_path = build_path_without("sed", farm_dir)
        cmd = "gh pr create --title 'Add agents' --body 'Adds reviewer agents.'"
        assert (
            run_hook(
                STOW_REMINDER_HOOK,
                bash_input(cmd),
                cwd=stow_repo,
                extra_env={"PATH": restricted_path},
            )
            == "allow"
        )

    def test_malformed_input_denied(self, stow_repo):
        """Fail-closed on unparseable JSON, parallel to the other gates."""
        result = subprocess.run(
            [str(STOW_REMINDER_HOOK)],
            input="not json",
            capture_output=True,
            text=True,
            cwd=stow_repo,
            check=False,
        )
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_missing_lib_sh_bootstrap_deny_cites_the_runbook_and_leaves_install_sh_to_the_user(self, tmp_path):
        """With `_lib.sh` absent, the bootstrap deny cites the deadlock-recovery runbook and says
        the user, not the agent, runs ./install.sh only if that runbook says so."""
        # The copy sits alone in `tmp_path`, so the hook finds no `_lib.sh` beside it.
        lonely_hook = tmp_path / STOW_REMINDER_HOOK.name
        shutil.copy2(STOW_REMINDER_HOOK, lonely_hook)
        lonely_hook.chmod(0o755)
        result = subprocess.run(
            [str(lonely_hook)],
            input=json.dumps(bash_input("gh pr create --title T --body 'no marker here'")),
            capture_output=True,
            text=True,
            cwd=tmp_path,
            check=False,
        )
        assert result.returncode == 2
        assert "could not source _lib.sh" in result.stderr
        assert "./install.sh" in result.stderr
        assert "the user, not the agent" in result.stderr
        assert "only if that runbook says so" in result.stderr
        assert "separate terminal" in result.stderr
        assert HOOKS_DOC_GATE_DEADLOCK_RECOVERY.text in result.stderr
        assert not re.search(r"re-?link", result.stderr, re.IGNORECASE)


class TestStowReminderDefersToReadme:
    """The deny offers an example PR-body line and cites the README heading."""

    def test_readme_has_the_updating_after_git_pull_heading(self):
        readme_text = (REPO_ROOT / "README.md").read_text()
        assert normalize_heading(README_UPDATING_AFTER_GIT_PULL) in heading_texts(readme_text), (
            f"README.md has no heading {README_UPDATING_AFTER_GIT_PULL!r}"
        )

    @pytest.mark.parametrize("commit_shape", TRIGGER_SHAPES)
    def test_example_body_line_used_as_pr_body_is_allowed(self, stow_repo, commit_shape):
        commit_shape(stow_repo)
        cmd = f"gh pr create --title T --body '{EXAMPLE_BODY_LINE}'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "allow"

    @pytest.mark.parametrize("commit_shape", TRIGGER_SHAPES)
    def test_deny_offers_the_example_body_line_and_cites_the_readme_heading(self, stow_repo, commit_shape):
        commit_shape(stow_repo)
        cmd = "gh pr create --title T --body 'no marker here'"
        reason = run_hook_reason(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo)
        assert reason is not None
        assert EXAMPLE_BODY_LINE in reason
        assert f"README.md § {README_UPDATING_AFTER_GIT_PULL}" in reason

    def test_deny_states_no_unconditional_rerun_rule(self, stow_repo):
        """The deny defers the rule to README and words the re-run as conditional, so it states no per-file re-run requirement."""
        commit_new_toplevel_dir(stow_repo, "agents")
        cmd = "gh pr create --title T --body 'no marker here'"
        reason = run_hook_reason(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo)
        assert reason is not None
        unconditional_rule_patterns = [
            r"individually",
            r"alone (does not|doesn't|won't)",
            r"only appears",
        ]
        found = [
            pattern
            for pattern in unconditional_rule_patterns
            if re.search(pattern, reason, re.IGNORECASE)
        ]
        assert not found, f"deny reason states an unconditional re-run rule: {found}"
