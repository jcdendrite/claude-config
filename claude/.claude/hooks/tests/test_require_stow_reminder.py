"""Tests for require-stow-reminder.sh."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    bash_input,
    run_hook,
)

STOW_REMINDER_HOOK = HOOKS_DIR / "require-stow-reminder.sh"


@pytest.fixture
def stow_repo(tmp_path):
    """A claude-config-shaped repo with `main` containing one already-
    stowed top-level entry under `claude/.claude/`, and a `feature`
    branch checked out for tests to add new content on top of.

    Tests that want a new top-level entry to be detected should add it
    on `feature` and commit there — the hook diffs `main...HEAD` from
    inside the repo's cwd."""
    repo = tmp_path / "stow-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:jcdendrite/claude-config.git"],
        cwd=repo,
        check=True,
    )
    # Existing top-level entry on main: claude/.claude/skills/foo.md.
    # Tests can add files inside `skills/` without tripping the gate
    # (skills/ is not a new top-level), or add a sibling like
    # `agents/` to trip it.
    skills = repo / "claude" / ".claude" / "skills"
    skills.mkdir(parents=True)
    (skills / "foo.md").write_text("# existing skill\n")
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
    """Add a file inside the already-stowed `skills/` directory. Should
    NOT trip the gate — `skills/` already exists on main."""
    (repo / "claude" / ".claude" / "skills" / "bar.md").write_text("# new skill\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add skill bar"], cwd=repo, check=True)


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
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
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

    def test_no_new_toplevel_allowed(self, stow_repo):
        """File added inside an already-stowed directory does not need
        a stow re-run; gate must not fire."""
        commit_inside_existing_toplevel(stow_repo)
        cmd = "gh pr create --title T --body 'just a new skill'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "allow"

    def test_new_toplevel_dir_without_marker_denied(self, stow_repo):
        commit_new_toplevel_dir(stow_repo, "agents")
        cmd = "gh pr create --title 'Add agents' --body 'Adds reviewer agents.'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "deny"

    def test_new_toplevel_file_without_marker_denied(self, stow_repo):
        """A new file directly under claude/.claude/ also requires
        re-stow — stow links each top-level child individually."""
        commit_new_toplevel_file(stow_repo, "newfile.md")
        cmd = "gh pr create --title T --body 'add file'"
        assert run_hook(STOW_REMINDER_HOOK, bash_input(cmd), cwd=stow_repo) == "deny"

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

    def test_no_main_ref_fails_open(self, tmp_path):
        """Fresh-clone state without a local `main` ref: hook must not
        block PR creation. Documented as a known fail-open in the
        header."""
        repo = tmp_path / "no-main"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "trunk"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
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
