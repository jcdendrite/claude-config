"""Tests for guard-settings-model-effort.sh."""
from __future__ import annotations

import subprocess

import pytest
from helpers import (
    HOOKS_DIR,
    bash_input,
    edit_input,
    run_hook,
    run_hook_reason,
    stage_settings,
)

GUARD_SETTINGS_MODEL_EFFORT_HOOK = HOOKS_DIR / "guard-settings-model-effort.sh"


@pytest.fixture
def settings_repo(tmp_path):
    """Git repo with a main branch and a staged settings.json change.

    Mirrors the structure the hook sees at commit time: a committed
    baseline on `main`, then a staged modification in the working tree.
    The repo path matches `claude/.claude/settings.json` — the exact
    path the hook checks for.
    """
    repo = tmp_path / "settings-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(
        ["git", "checkout", "-b", "main"],
        cwd=repo, check=True, capture_output=True,
    )
    # Create the settings.json at the repo-relative path the hook checks.
    settings_dir = repo / "claude" / ".claude"
    settings_dir.mkdir(parents=True)
    settings_file = settings_dir / "settings.json"
    settings_file.write_text('{"model": "sonnet", "effortLevel": "normal"}\n')
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo, settings_file


class TestGuardSettingsModelEffort:
    def test_model_change_denies_commit(self, settings_repo):
        repo, settings_file = settings_repo
        stage_settings(repo, settings_file, '{"model": "opus", "effortLevel": "normal"}\n')
        assert (
            run_hook(
                GUARD_SETTINGS_MODEL_EFFORT_HOOK,
                bash_input("git commit -m 'update settings'"),
                cwd=repo,
            )
            == "deny"
        )

    def test_effort_level_change_denies_commit(self, settings_repo):
        repo, settings_file = settings_repo
        stage_settings(repo, settings_file, '{"model": "sonnet", "effortLevel": "high"}\n')
        assert (
            run_hook(
                GUARD_SETTINGS_MODEL_EFFORT_HOOK,
                bash_input("git commit -m 'update settings'"),
                cwd=repo,
            )
            == "deny"
        )

    def test_both_changed_denies_commit(self, settings_repo):
        repo, settings_file = settings_repo
        stage_settings(repo, settings_file, '{"model": "opus", "effortLevel": "high"}\n')
        assert (
            run_hook(
                GUARD_SETTINGS_MODEL_EFFORT_HOOK,
                bash_input("git commit -m 'routing change'"),
                cwd=repo,
            )
            == "deny"
        )

    def test_skip_auto_permission_prompt_change_denies_commit(self, settings_repo):
        """skipAutoPermissionPrompt, written automatically by Claude Code, must block."""
        repo, settings_file = settings_repo
        stage_settings(
            repo,
            settings_file,
            '{"model": "sonnet", "effortLevel": "normal", "skipAutoPermissionPrompt": true}\n',
        )
        assert (
            run_hook(
                GUARD_SETTINGS_MODEL_EFFORT_HOOK,
                bash_input("git commit -m 'update settings'"),
                cwd=repo,
            )
            == "deny"
        )

    def test_unrelated_settings_change_allows(self, settings_repo):
        """Changing a key other than the guarded set must not block."""
        repo, settings_file = settings_repo
        stage_settings(repo, settings_file, '{"model": "sonnet", "effortLevel": "normal", "theme": "dark"}\n')
        assert (
            run_hook(
                GUARD_SETTINGS_MODEL_EFFORT_HOOK,
                bash_input("git commit -m 'add theme'"),
                cwd=repo,
            )
            == "allow"
        )

    def test_settings_not_staged_allows(self, settings_repo):
        """If settings.json is not staged, the hook has no opinion."""
        repo, settings_file = settings_repo
        # Stage a different file, not settings.json.
        other = repo / "other.txt"
        other.write_text("change\n")
        subprocess.run(["git", "add", "other.txt"], cwd=repo, check=True)
        assert (
            run_hook(
                GUARD_SETTINGS_MODEL_EFFORT_HOOK,
                bash_input("git commit -m 'other change'"),
                cwd=repo,
            )
            == "allow"
        )

    def test_non_commit_command_allows(self, settings_repo):
        """Hook only fires on git commit; other commands pass through."""
        repo, settings_file = settings_repo
        stage_settings(repo, settings_file, '{"model": "opus"}\n')
        assert (
            run_hook(
                GUARD_SETTINGS_MODEL_EFFORT_HOOK,
                bash_input("git status"),
                cwd=repo,
            )
            == "allow"
        )

    def test_non_bash_tool_allows(self, settings_repo):
        """Edit/Write tool calls pass through — hook is Bash-only."""
        repo, settings_file = settings_repo
        assert (
            run_hook(
                GUARD_SETTINGS_MODEL_EFFORT_HOOK,
                edit_input(str(settings_file)),
                cwd=repo,
            )
            == "allow"
        )

    def test_deny_message_mentions_settings_json(self, settings_repo):
        """Deny reason must reference settings.json so the agent knows what to unstage."""
        repo, settings_file = settings_repo
        stage_settings(repo, settings_file, '{"model": "opus", "effortLevel": "normal"}\n')
        reason = run_hook_reason(
            GUARD_SETTINGS_MODEL_EFFORT_HOOK,
            bash_input("git commit -m 'update settings'"),
            cwd=repo,
        )
        assert reason is not None
        assert "settings.json" in reason
        assert "model" in reason or "effortLevel" in reason

    def test_outside_git_repo_allows(self, tmp_path):
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        assert (
            run_hook(
                GUARD_SETTINGS_MODEL_EFFORT_HOOK,
                bash_input("git commit -m foo"),
                cwd=non_repo,
            )
            == "allow"
        )

    def test_chained_add_commit_with_model_change_denies(self, settings_repo):
        """Chained `git add ... && git commit` is still gated."""
        repo, settings_file = settings_repo
        stage_settings(repo, settings_file, '{"model": "haiku", "effortLevel": "normal"}\n')
        assert (
            run_hook(
                GUARD_SETTINGS_MODEL_EFFORT_HOOK,
                bash_input("git add . && git commit -m update"),
                cwd=repo,
            )
            == "deny"
        )

    def test_empty_staged_diff_allows(self, settings_repo):
        """No staged changes → let git decide (nothing staged case)."""
        repo, settings_file = settings_repo
        # Ensure nothing is staged.
        subprocess.run(["git", "reset", "HEAD", "--", "."], cwd=repo, check=True)
        assert (
            run_hook(
                GUARD_SETTINGS_MODEL_EFFORT_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "allow"
        )
