"""Tests for require-worktree-for-file-writes.sh."""
from __future__ import annotations

import json
import subprocess

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
    def test_no_sentinel_allows_edit(self, non_opted_repo):
        """Repo without the sentinel: Edit passes through unconditionally."""
        path = str(non_opted_repo / "file.txt")
        assert run_hook(FILE_WRITES_HOOK, edit_input(path)) == "allow"

    def test_no_sentinel_allows_write(self, non_opted_repo):
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

    def test_opted_in_worktree_allows_edit(self, opted_in_with_worktree):
        """Edit targeting a file inside a linked worktree is allowed."""
        _, worktree = opted_in_with_worktree
        path = str(worktree / "file.txt")
        assert run_hook(FILE_WRITES_HOOK, edit_input(path)) == "allow"

    def test_opted_in_worktree_allows_write(self, opted_in_with_worktree):
        """Write targeting a new file inside a linked worktree is allowed."""
        _, worktree = opted_in_with_worktree
        path = str(worktree / "new.txt")
        assert run_hook(FILE_WRITES_HOOK, write_input(path)) == "allow"

    def test_opted_in_worktree_allows_multiedit(self, opted_in_with_worktree):
        """MultiEdit targeting a file inside a linked worktree is allowed."""
        _, worktree = opted_in_with_worktree
        path = str(worktree / "file.txt")
        assert run_hook(FILE_WRITES_HOOK, multiedit_input(path)) == "allow"

    def test_non_git_path_allows_edit(self, tmp_path):
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

    def test_bash_tool_allowed(self, opted_in_repo):
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
