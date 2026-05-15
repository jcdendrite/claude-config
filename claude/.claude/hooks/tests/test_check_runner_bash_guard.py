"""Tests for check-runner-bash-guard.sh."""
from __future__ import annotations

import pytest
from helpers import (
    HOOKS_DIR,
    bash_input,
    run_hook,
    run_hook_reason,
)

GUARD_HOOK = HOOKS_DIR / "check-runner-bash-guard.sh"


class TestCheckRunnerBashGuardAllows:
    @pytest.mark.parametrize(
        "command",
        [
            "git status",
            "git diff HEAD~1",
            "git log --oneline -10",
            "git show HEAD",
            "git fetch origin",
            "git branch",
            "git rev-parse --show-toplevel",
            "git remote -v",
            "git blame file.txt",
            "git ls-files",
            "git ls-tree HEAD",
            "git ls-remote origin",
            "git reflog",
            "git shortlog -sn",
            "git describe --tags",
            "git for-each-ref",
            "git fsck",
            "git rev-list HEAD",
            "git verify-commit HEAD",
            "git version",
            "git worktree list",
            "git tag",
            "git var GIT_AUTHOR_IDENT",
        ],
    )
    def test_readonly_git_commands_allowed(self, command):
        assert run_hook(GUARD_HOOK, bash_input(command)) == "allow"

    @pytest.mark.parametrize(
        "command",
        [
            "pytest claude/.claude/",
            "ruff check claude/.claude/",
            "npm run verify",
            "supabase db reset",
            "echo hello",
            "cat file.txt",
            "ls -la",
            "python -m pytest",
        ],
    )
    def test_non_git_commands_pass_through(self, command):
        assert run_hook(GUARD_HOOK, bash_input(command)) == "allow"

    def test_chained_readonly_git_allowed(self):
        assert run_hook(GUARD_HOOK, bash_input("git status && git diff")) == "allow"

    def test_git_in_path_does_not_trigger(self):
        assert run_hook(GUARD_HOOK, bash_input("ls .github/workflows/")) == "allow"

    def test_gitignore_does_not_trigger(self):
        assert run_hook(GUARD_HOOK, bash_input("cat .gitignore")) == "allow"


class TestCheckRunnerBashGuardDenies:
    @pytest.mark.parametrize(
        "command",
        [
            "git commit -m 'fix tests'",
            "git push origin main",
            "git checkout -- path/to/file",
            "git reset --hard HEAD~1",
            "git rebase origin/main",
            "git merge main",
            "git stash",
            "git apply patch.diff",
            "git cherry-pick abc123",
            "git clean -fd",
            "git add .",
            "git rm file.txt",
            "git mv old.txt new.txt",
        ],
    )
    def test_write_git_commands_denied(self, command):
        assert run_hook(GUARD_HOOK, bash_input(command)) == "deny"

    def test_deny_message_instructs_return_verdict(self):
        reason = run_hook_reason(GUARD_HOOK, bash_input("git commit -m fix"))
        assert reason is not None
        assert "return the verdict" in reason

    def test_deny_message_names_the_subcommand(self):
        reason = run_hook_reason(GUARD_HOOK, bash_input("git push origin main"))
        assert reason is not None
        assert "push" in reason

    def test_chained_write_after_readonly_denied(self):
        assert run_hook(GUARD_HOOK, bash_input("git status && git commit -m fix")) == "deny"

    def test_chained_write_before_readonly_denied(self):
        assert run_hook(GUARD_HOOK, bash_input("git commit -m fix && git status")) == "deny"


class TestCheckRunnerBashGuardFailClosed:
    def test_malformed_json_denied(self):
        import json
        import subprocess
        result = subprocess.run(
            [str(GUARD_HOOK)],
            input="not-json",
            capture_output=True,
            text=True,
            check=False,
        )
        # Fail-closed: jq parse failure sets JQ_EXIT nonzero and emit_deny fires
        # before the regex fast-path. Malformed input must always produce deny.
        assert result.returncode == 0
        assert result.stdout.strip(), "expected deny JSON, got empty output (allow)"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_empty_command_allowed(self):
        assert run_hook(GUARD_HOOK, bash_input("")) == "allow"
