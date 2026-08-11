"""Tests for guard-settings-session-keys.sh."""
from __future__ import annotations

import re
import shutil
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

GUARD_SETTINGS_SESSION_KEYS_HOOK = HOOKS_DIR / "guard-settings-session-keys.sh"

# The deny reason is prose, and the key list is the only structured thing in
# it, so pull that segment out and compare as a set — asserting on the raw
# sentence would fail on a reworded message or a reordered GUARDED_KEYS_JSON
# without the hook's behavior having changed.
_CHANGED_KEYS_SEGMENT = re.compile(r"differs from main on: ([^.]*)\.")


def names_changed_keys(reason: str | None) -> set[str]:
    """Return the guarded key names the deny reason reports as changed."""
    assert reason is not None, "hook allowed the commit; expected a deny reason"
    match = _CHANGED_KEYS_SEGMENT.search(reason)
    assert match is not None, f"deny reason did not name the changed keys: {reason}"
    return set(match.group(1).split())


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


class TestGuardSettingsSessionKeys:
    def test_model_change_denies_commit(self, settings_repo):
        repo, settings_file = settings_repo
        stage_settings(repo, settings_file, '{"model": "opus", "effortLevel": "normal"}\n')
        assert (
            run_hook(
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
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
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
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
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
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
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
                bash_input("git commit -m 'update settings'"),
                cwd=repo,
            )
            == "deny"
        )

    def test_skip_workflow_usage_warning_change_denies_commit(self, settings_repo):
        """skipWorkflowUsageWarning, a Claude-Code-persisted dismissal, must block."""
        repo, settings_file = settings_repo
        stage_settings(
            repo,
            settings_file,
            '{"model": "sonnet", "effortLevel": "normal", "skipWorkflowUsageWarning": true}\n',
        )
        assert (
            run_hook(
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
                bash_input("git commit -m 'update settings'"),
                cwd=repo,
            )
            == "deny"
        )

    def test_theme_change_denies_commit(self, settings_repo):
        """theme is one machine's UI preference — committing ships it to every user."""
        repo, settings_file = settings_repo
        stage_settings(
            repo,
            settings_file,
            '{"model": "sonnet", "effortLevel": "normal", "theme": "dark"}\n',
        )
        assert (
            run_hook(
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
                bash_input("git commit -m 'add theme'"),
                cwd=repo,
            )
            == "deny"
        )

    def test_tui_change_denies_commit(self, settings_repo):
        """tui is one machine's UI preference — committing ships it to every user."""
        repo, settings_file = settings_repo
        stage_settings(
            repo,
            settings_file,
            '{"model": "sonnet", "effortLevel": "normal", "tui": "fullscreen"}\n',
        )
        assert (
            run_hook(
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
                bash_input("git commit -m 'add tui mode'"),
                cwd=repo,
            )
            == "deny"
        )

    def test_unrelated_settings_change_allows(self, settings_repo):
        """Changing a key outside the guarded set must not block.

        Uses a name the settings schema will never claim, so the test cannot
        be silently invalidated by that key later becoming guarded.
        """
        repo, settings_file = settings_repo
        stage_settings(
            repo,
            settings_file,
            '{"model": "sonnet", "effortLevel": "normal",'
            ' "unrelatedTestKey": "value"}\n',
        )
        assert (
            run_hook(
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
                bash_input("git commit -m 'add unrelated key'"),
                cwd=repo,
            )
            == "allow"
        )

    def test_guarded_key_added_where_main_lacks_it_denies(self, settings_repo):
        """A guarded key absent from main and present in staged must block.

        This is the realistic shape: Claude Code adds a key to a settings.json
        whose committed baseline predates it.
        """
        repo, settings_file = settings_repo
        stage_settings(
            repo,
            settings_file,
            '{"model": "sonnet", "effortLevel": "normal", "tui": "fullscreen"}\n',
        )
        assert (
            run_hook(
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
                bash_input("git commit -m 'key absent from main'"),
                cwd=repo,
            )
            == "deny"
        )

    def test_guarded_key_set_to_false_against_absent_denies(self, settings_repo):
        """An explicit false must not read as equal to the key being absent.

        `// ""` would collapse false, null, and absent to one value; the
        comparator distinguishes presence from value so booleans stay guarded.
        """
        repo, settings_file = settings_repo
        stage_settings(
            repo,
            settings_file,
            '{"model": "sonnet", "effortLevel": "normal",'
            ' "skipWorkflowUsageWarning": false}\n',
        )
        assert (
            run_hook(
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
                bash_input("git commit -m 'explicit false'"),
                cwd=repo,
            )
            == "deny"
        )

    def test_guarded_key_set_to_null_against_absent_denies(self, settings_repo):
        """An explicit null must not read as equal to the key being absent."""
        repo, settings_file = settings_repo
        stage_settings(
            repo,
            settings_file,
            '{"model": "sonnet", "effortLevel": "normal", "theme": null}\n',
        )
        assert (
            run_hook(
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
                bash_input("git commit -m 'explicit null'"),
                cwd=repo,
            )
            == "deny"
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
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
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
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
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
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
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
            GUARD_SETTINGS_SESSION_KEYS_HOOK,
            bash_input("git commit -m 'update settings'"),
            cwd=repo,
        )
        assert reason is not None
        assert "settings.json" in reason
        assert "model" in reason or "effortLevel" in reason

    def test_deny_message_names_only_the_changed_keys(self, settings_repo):
        """The message names which guarded keys actually differ, not the whole set."""
        repo, settings_file = settings_repo
        stage_settings(
            repo,
            settings_file,
            '{"model": "sonnet", "effortLevel": "normal", "theme": "dark"}\n',
        )
        reason = run_hook_reason(
            GUARD_SETTINGS_SESSION_KEYS_HOOK,
            bash_input("git commit -m 'add theme'"),
            cwd=repo,
        )
        assert names_changed_keys(reason) == {"theme"}

    def test_deny_message_names_multiple_changed_keys(self, settings_repo):
        """Every guarded key that differs is named, and no key that does not."""
        repo, settings_file = settings_repo
        stage_settings(
            repo,
            settings_file,
            '{"model": "opus", "effortLevel": "normal", "tui": "fullscreen"}\n',
        )
        reason = run_hook_reason(
            GUARD_SETTINGS_SESSION_KEYS_HOOK,
            bash_input("git commit -m 'model and tui'"),
            cwd=repo,
        )
        assert names_changed_keys(reason) == {"model", "tui"}

    def test_object_valued_guarded_key_ignores_key_order(self, settings_repo):
        """Reordering an object-valued guarded key's own keys is not a change.

        Comparing stringified values would report this as changed, since
        neither jq's tostring nor tojson canonicalizes object key order.
        """
        repo, settings_file = settings_repo
        settings_file.write_text('{"model": "sonnet", "tui": {"a": 1, "b": 2}}\n')
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "object-valued baseline"],
            cwd=repo, check=True,
        )
        stage_settings(
            repo, settings_file, '{"model": "sonnet", "tui": {"b": 2, "a": 1}}\n'
        )
        assert (
            run_hook(
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
                bash_input("git commit -m 'reorder object keys'"),
                cwd=repo,
            )
            == "allow"
        )

    def test_malformed_staged_settings_denies(self, settings_repo):
        """Unparseable staged content degrades to {}, so main's keys read as changed.

        The gate is fail-open only when jq itself cannot run — content that
        does not parse still blocks rather than passing silently.
        """
        repo, settings_file = settings_repo
        stage_settings(repo, settings_file, '{"model": "sonnet", "effortLev')
        assert (
            run_hook(
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
                bash_input("git commit -m 'truncated settings'"),
                cwd=repo,
            )
            == "deny"
        )

    def test_outside_git_repo_allows(self, tmp_path):
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        assert (
            run_hook(
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
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
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
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
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
                bash_input("git commit -m foo"),
                cwd=repo,
            )
            == "allow"
        )

    def _stub_bin_without_timeout(self, tmp_path):
        """Stub PATH with only the binaries this hook's code path invokes
        (`cat`/`jq` via _lib.sh's JSON parsing, `dirname` to locate _lib.sh,
        `grep` for the git-commit/staged-file matches, `git` for the
        _lib_capped-wrapped diff/show calls), omitting both timeout(1) and
        gtimeout(1). Mirrors test_require_worktree_for_git_writes.py's
        test_python3_absent_denies shape; skips (does not silently
        under-symlink) when a needed real binary is itself absent."""
        stub_bin = tmp_path / "_stub_bin"
        stub_bin.mkdir()
        for tool in ("cat", "dirname", "git", "grep", "jq"):
            real_path = shutil.which(tool)
            if not real_path:
                pytest.skip(f"{tool} not found in PATH")
            (stub_bin / tool).symlink_to(real_path)
        return stub_bin

    def test_guarded_key_change_denies_when_neither_timeout_nor_gtimeout_present(
        self, settings_repo, tmp_path
    ):
        """Fail-open regression: with neither binary present, _lib_capped
        runs git uncapped (see _lib.sh) rather than silently skipping — the
        gate must still catch a guarded-key change under this PATH."""
        repo, settings_file = settings_repo
        stage_settings(repo, settings_file, '{"model": "opus", "effortLevel": "normal"}\n')
        stub_bin = self._stub_bin_without_timeout(tmp_path)
        assert (
            run_hook(
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
                bash_input("git commit -m 'update settings'"),
                cwd=repo,
                extra_env={"PATH": str(stub_bin)},
            )
            == "deny"
        )

    def test_unrelated_settings_change_allows_when_neither_timeout_nor_gtimeout_present(
        self, settings_repo, tmp_path
    ):
        """Companion allow case for the deny above: under the same PATH, a
        non-guarded change must still pass — without this, a fallback branch
        that always returns nonzero would masquerade as a working gate."""
        repo, settings_file = settings_repo
        stage_settings(
            repo,
            settings_file,
            '{"model": "sonnet", "effortLevel": "normal",'
            ' "unrelatedTestKey": "value"}\n',
        )
        stub_bin = self._stub_bin_without_timeout(tmp_path)
        assert (
            run_hook(
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
                bash_input("git commit -m 'add unrelated key'"),
                cwd=repo,
                extra_env={"PATH": str(stub_bin)},
            )
            == "allow"
        )
