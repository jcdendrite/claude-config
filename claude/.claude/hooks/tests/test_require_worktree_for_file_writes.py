"""Tests for require-worktree-for-file-writes.sh."""
from __future__ import annotations

import json
import subprocess

import pytest
from helpers import (
    HOOKS_DIR,
    bash_input,
    edit_input,
    multiedit_input,
    run_hook,
    run_hook_reason,
    write_input,
)

FILE_WRITES_HOOK = HOOKS_DIR / "require-worktree-for-file-writes.sh"


class TestRequireWorktreeForFileWrites:
    def test_no_sentinel_allows_edit(self, non_opted_repo, isolated_home):
        """Repo without the sentinel: Edit passes through unconditionally."""
        path = str(non_opted_repo / "file.txt")
        assert run_hook(FILE_WRITES_HOOK, edit_input(path)) == "allow"

    def test_no_sentinel_allows_write(self, non_opted_repo, isolated_home):
        """Repo without the sentinel: Write passes through unconditionally."""
        path = str(non_opted_repo / "new.txt")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "allow"

    def test_opted_in_main_tree_denies_edit(self, opted_in_repo):
        """Edit targeting an existing file in the main tree is denied."""
        path = str(opted_in_repo / "file.txt")
        assert run_hook(FILE_WRITES_HOOK, edit_input(path)) == "deny"

    def test_opted_in_main_tree_denies_write(self, opted_in_repo):
        """Write targeting the main tree is denied even for a new file."""
        path = str(opted_in_repo / "newfile.txt")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "deny"

    def test_opted_in_main_tree_denies_multiedit(self, opted_in_repo):
        """MultiEdit targeting the main tree is denied."""
        path = str(opted_in_repo / "file.txt")
        assert run_hook(FILE_WRITES_HOOK, multiedit_input(path)) == "deny"

    def test_opted_in_worktree_allows_edit(self, isolated_home, opted_in_with_worktree):
        """Edit targeting a file inside a linked worktree is allowed."""
        _, worktree = opted_in_with_worktree
        path = str(worktree / "file.txt")
        assert run_hook(FILE_WRITES_HOOK, edit_input(path)) == "allow"

    def test_opted_in_worktree_allows_write(self, isolated_home, opted_in_with_worktree):
        """Write targeting a new file inside a linked worktree is allowed."""
        _, worktree = opted_in_with_worktree
        path = str(worktree / "new.txt")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "allow"

    def test_opted_in_worktree_allows_multiedit(self, isolated_home, opted_in_with_worktree):
        """MultiEdit targeting a file inside a linked worktree is allowed."""
        _, worktree = opted_in_with_worktree
        path = str(worktree / "file.txt")
        assert run_hook(FILE_WRITES_HOOK, multiedit_input(path)) == "allow"

    def test_non_git_path_allows_edit(self, tmp_path, isolated_home):
        """Edit to a path outside any git repo is allowed."""
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        path = str(non_repo / "file.txt")
        assert run_hook(FILE_WRITES_HOOK, edit_input(path)) == "allow"

    def test_new_file_nested_path_denied_in_main_tree(self, opted_in_repo):
        """Write to a not-yet-existing nested path whose ancestor is in the
        main tree is denied — the hook must walk up to the existing dir."""
        path = str(opted_in_repo / "subdir" / "deeply" / "new.txt")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "deny"

    def test_bash_tool_allowed(self, isolated_home, opted_in_repo):
        """Non-file-write tool (Bash) passes through: the hook is scoped to
        Edit/Write/MultiEdit only."""
        assert run_hook(FILE_WRITES_HOOK, bash_input("echo hi")) == "allow"

    def test_deny_message_names_relative_path(self, opted_in_repo):
        """Deny message should include the relative worktree path hint."""
        path = str(opted_in_repo / "src" / "main.sh")
        reason = run_hook_reason(FILE_WRITES_HOOK, edit_input(path))
        assert reason is not None
        assert "src/main.sh" in reason

    def test_deny_message_names_tool(self, opted_in_repo):
        """Deny message should name the tool that was blocked."""
        path = str(opted_in_repo / "file.txt")
        reason = run_hook_reason(FILE_WRITES_HOOK, write_input(path))
        assert reason is not None
        assert "Write" in reason

    def test_malformed_json_stdin_denies(self):
        """Malformed JSON input must produce a deny, not a silent allow."""
        result = subprocess.run(
            [str(FILE_WRITES_HOOK)],
            input="not-json{{{",
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.stdout.strip(), "Expected deny output on malformed JSON, got silent allow"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"


class TestRequireWorktreeForFileWritesHomeExemption:
    """The hook must not block writes to ~/.claude/ even when $HOME resolves
    into an opted-in repo via stow directory-folding."""

    @pytest.fixture
    def opted_in_home(self, tmp_path, monkeypatch):
        """Sandboxed $HOME that is itself a git repo with worktree-required
        committed — reproduces the stow directory-fold scenario where
        ~/.claude/ resolves into an opted-in claude-config checkout."""
        home = tmp_path / "home"
        home.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=home, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=home, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=home, check=True)
        (home / ".claude").mkdir()
        (home / ".claude" / "worktree-required").write_text("# sentinel\n")
        (home / ".claude" / "plans").mkdir()
        (home / ".claude" / "plans" / "existing.md").write_text("plan content\n")
        subprocess.run(["git", "add", "."], cwd=home, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=home, check=True)
        monkeypatch.setenv("HOME", str(home))
        return home

    def test_home_claude_existing_file_allowed(self, opted_in_home):
        """Write to an existing ~/.claude/ file is allowed despite the repo
        being opted into worktree discipline."""
        path = str(opted_in_home / ".claude" / "plans" / "existing.md")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "allow"

    def test_home_claude_new_file_allowed(self, opted_in_home):
        """Write to a not-yet-existing ~/.claude/ file is allowed. The
        original failure manifested on new-file writes where the hook's
        dirname-walk ascended out of the missing path into the repo root."""
        path = str(opted_in_home / ".claude" / "plans" / "new.md")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "allow"

    def test_non_claude_path_in_opted_in_home_denied(self, opted_in_home):
        """A write to a non-.claude path inside the same opted-in home repo
        is still denied — the exemption is scoped to ~/.claude/, not the
        entire $HOME repo."""
        path = str(opted_in_home / "some-project-file.txt")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "deny"

    def test_adjacent_prefix_not_exempt(self, opted_in_home):
        """~/.claude-foo/ must not match the ~/.claude/ exemption — the
        case glob has a literal '/' after '.claude', so .claude-foo cannot
        satisfy it."""
        (opted_in_home / ".claude-foo").mkdir()
        (opted_in_home / ".claude-foo" / "file.md").write_text("")
        path = str(opted_in_home / ".claude-foo" / "file.md")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "deny"

    def test_exact_dotclaude_dir_not_exempt(self, opted_in_home):
        """A write path of exactly $HOME/.claude (no trailing segment) does
        not satisfy the '/.claude/*' glob and falls through to repo-walk
        denial."""
        path = str(opted_in_home / ".claude")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "deny"

    def test_dot_dot_traversal_not_exempt(self, opted_in_home):
        """A file_path using '..' traversal through .claude/ must not be
        exempted. The case glob matches on the raw string, so
        $HOME/.claude/../other-file would satisfy the prefix without
        actually resolving inside $HOME/.claude/. The traversal guard
        rejects any path containing '/..' before the prefix check."""
        path = str(opted_in_home / ".claude" / ".." / "project-file.txt")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "deny"

    def test_stow_symlinked_claude_dir_allows(self, opted_in_home, tmp_path):
        """When ~/.claude/ is a stow-managed symlink to a repo's .claude/
        directory (directory-fold), writes through the symlink path must
        still be allowed. realpath would resolve the path into the repo root,
        breaking the fix — the hook intentionally uses the raw string."""
        # Simulate stow directory-fold: ~/.claude is a symlink to another
        # opted-in repo's .claude/ dir.
        target_repo = tmp_path / "target-repo"
        target_repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=target_repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=target_repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=target_repo, check=True)
        dot_claude = target_repo / ".claude"
        dot_claude.mkdir()
        (dot_claude / "worktree-required").write_text("# sentinel\n")
        (dot_claude / "settings.json").write_text("{}\n")
        subprocess.run(["git", "add", "."], cwd=target_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=target_repo, check=True)
        # Replace ~/.claude dir with a symlink to target_repo/.claude/
        (opted_in_home / ".claude").rename(opted_in_home / ".claude-orig")
        (opted_in_home / ".claude").symlink_to(dot_claude)
        # Write to ~/.claude/settings.json via the raw symlink path
        path = str(opted_in_home / ".claude" / "settings.json")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "allow"


class TestMachineLevelMarker:
    """Tests for the machine-level ~/.claude/worktree-required marker."""

    def test_machine_marker_enforces_on_main_tree(self, non_opted_repo, user_marker_home):
        """Machine marker active + main tree → deny."""
        path = str(non_opted_repo / "file.txt")
        assert run_hook(FILE_WRITES_HOOK, edit_input(path)) == "deny"

    def test_machine_marker_plus_optout_allows(self, repo_with_optout, user_marker_home):
        """Machine marker active + repo opt-out → allow."""
        path = str(repo_with_optout / "f.txt")
        assert run_hook(FILE_WRITES_HOOK, edit_input(path)) == "allow"

    def test_repo_marker_plus_optout_still_enforces(self, opted_in_repo, user_marker_home):
        """Committed repo marker + opt-out → still deny (opt-out can't defeat committed marker)."""
        (opted_in_repo / ".claude" / "worktree-optout").write_text("# opt-out\n")
        path = str(opted_in_repo / "f.txt")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "deny"

    def test_neither_marker_allows(self, non_opted_repo, isolated_home):
        """No markers at all → allow."""
        path = str(non_opted_repo / "file.txt")
        assert run_hook(FILE_WRITES_HOOK, edit_input(path)) == "allow"

    def test_optout_alone_is_inert(self, repo_with_optout, isolated_home):
        """Opt-out present but no machine marker and no repo marker → allow."""
        path = str(repo_with_optout / "f.txt")
        assert run_hook(FILE_WRITES_HOOK, edit_input(path)) == "allow"

    def test_machine_marker_outside_git_repo_allows(self, tmp_path, user_marker_home):
        """Machine marker active + path outside any git repo → allow."""
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        path = str(non_repo / "file.txt")
        assert run_hook(FILE_WRITES_HOOK, edit_input(path)) == "allow"

    def test_machine_marker_home_path_still_exempt(self, non_opted_repo, user_marker_home):
        """Machine marker active but write targets ~/.claude/foo → allow (HOME exemption holds)."""
        # user_marker_home is the sandboxed HOME; write to something under it
        path = str(user_marker_home / ".claude" / "some-file.txt")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "allow"
