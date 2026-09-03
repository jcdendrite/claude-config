"""Tests for guard-settings-session-keys.sh."""
from __future__ import annotations

import re
import shutil
import subprocess

import pytest
from helpers import (
    HOOKS_DIR,
    bash_input,
    build_path_without,
    edit_input,
    run_hook,
    run_hook_reason,
    stage_settings,
)

GUARD_SETTINGS_SESSION_KEYS_HOOK = HOOKS_DIR / "guard-settings-session-keys.sh"

# The deny reason is prose, and the key list is the only structured thing in
# it, so pull that segment out and compare as a set — asserting on the raw
# sentence would fail on a reworded message or a reordered GUARDED_KEYS_JSON
# without the hook's behavior having changed. Matches up to the literal
# ". These keys" that follows the list, not the first bare "." — a nested
# guarded key's own dotted path (e.g. "env.CLAUDE_CODE_EFFORT_LEVEL")
# contains a "." that a naive "stop at first period" pattern would truncate on.
_CHANGED_KEYS_SEGMENT = re.compile(r"differs from main on: (.*?)\. These keys")


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

    def test_deny_message_names_nested_key_by_its_dotted_path(self, settings_repo):
        """Nested mirror of test_deny_message_names_only_the_changed_keys:
        the message names the guarded key by its full dotted path, not just
        the leaf or the `env` parent."""
        repo, settings_file = settings_repo
        stage_settings(
            repo,
            settings_file,
            '{"model": "sonnet", "effortLevel": "normal",'
            ' "env": {"CLAUDE_CODE_EFFORT_LEVEL": "high"}}\n',
        )
        reason = run_hook_reason(
            GUARD_SETTINGS_SESSION_KEYS_HOOK,
            bash_input("git commit -m 'nested effort level'"),
            cwd=repo,
        )
        assert names_changed_keys(reason) == {"env.CLAUDE_CODE_EFFORT_LEVEL"}

    def test_deny_message_names_both_nested_keys(self, settings_repo):
        """Nested mirror of test_deny_message_names_multiple_changed_keys."""
        repo, settings_file = settings_repo
        stage_settings(
            repo,
            settings_file,
            '{"model": "sonnet", "effortLevel": "normal",'
            ' "env": {"CLAUDE_CODE_EFFORT_LEVEL": "high", "ANTHROPIC_MODEL": "opus"}}\n',
        )
        reason = run_hook_reason(
            GUARD_SETTINGS_SESSION_KEYS_HOOK,
            bash_input("git commit -m 'nested routing override'"),
            cwd=repo,
        )
        assert names_changed_keys(reason) == {
            "env.CLAUDE_CODE_EFFORT_LEVEL", "env.ANTHROPIC_MODEL",
        }

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
        `sed`/`tr` for _lib_command_invokes_git_subcmd's git-commit match
        (GH-783), `grep` for the staged-file match, `git` for the
        _lib_capped-wrapped diff/show calls), omitting both timeout(1) and
        gtimeout(1). Mirrors test_require_worktree_for_git_writes.py's
        test_python3_absent_denies shape; skips (does not silently
        under-symlink) when a needed real binary is itself absent."""
        stub_bin = tmp_path / "_stub_bin"
        stub_bin.mkdir()
        for tool in ("cat", "dirname", "git", "grep", "jq", "sed", "tr"):
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

    def test_sed_absent_from_path_still_allows(self, settings_repo, tmp_path):
        """Mirror-image of the checked hooks' status-2 deny tests: this
        hook is deliberately, correctly unchecked (see its header's fail-
        open posture), so a guarded-key change under a sed-absent PATH
        must still ALLOW -- the fast-reject can't determine a match,
        treats that the same as "no match", and the guarded-key check
        below never runs. Pins the accepted fail-open posture as an
        executable invariant rather than leaving it implicit."""
        repo, settings_file = settings_repo
        stage_settings(repo, settings_file, '{"model": "opus", "effortLevel": "normal"}\n')
        farm_dir = tmp_path / "path-without-sed"
        farm_dir.mkdir()
        restricted_path = build_path_without("sed", farm_dir)
        assert (
            run_hook(
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
                bash_input("git commit -m 'update settings'"),
                cwd=repo,
                extra_env={"PATH": restricted_path},
            )
            == "allow"
        )

    def test_nested_effort_level_added_where_env_absent_denies(self, settings_repo):
        """The realistic shape: a fresh `env` block written by `/effort`,
        with no `env` key on main at all."""
        repo, settings_file = settings_repo
        stage_settings(
            repo,
            settings_file,
            '{"model": "sonnet", "effortLevel": "normal",'
            ' "env": {"CLAUDE_CODE_EFFORT_LEVEL": "high"}}\n',
        )
        assert (
            run_hook(
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
                bash_input("git commit -m 'nested effort level'"),
                cwd=repo,
            )
            == "deny"
        )

    def test_nested_effort_level_changed_denies(self, settings_repo):
        repo, settings_file = settings_repo
        stage_settings(
            repo,
            settings_file,
            '{"model": "sonnet", "effortLevel": "normal",'
            ' "env": {"CLAUDE_CODE_EFFORT_LEVEL": "high"}}\n',
        )
        subprocess.run(
            ["git", "commit", "-am", "baseline with nested env"],
            cwd=repo, check=True, capture_output=True,
        )
        stage_settings(
            repo,
            settings_file,
            '{"model": "sonnet", "effortLevel": "normal",'
            ' "env": {"CLAUDE_CODE_EFFORT_LEVEL": "low"}}\n',
        )
        assert (
            run_hook(
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
                bash_input("git commit -m 'change nested effort level'"),
                cwd=repo,
            )
            == "deny"
        )

    def test_nested_anthropic_model_added_denies(self, settings_repo):
        repo, settings_file = settings_repo
        stage_settings(
            repo,
            settings_file,
            '{"model": "sonnet", "effortLevel": "normal",'
            ' "env": {"ANTHROPIC_MODEL": "opus"}}\n',
        )
        assert (
            run_hook(
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
                bash_input("git commit -m 'nested model override'"),
                cwd=repo,
            )
            == "deny"
        )

    def test_nested_keys_both_changed_denies(self, settings_repo):
        """Both nested keys changed against pre-existing values in one
        commit — the production shape (`/effort` and a model override both
        landing on top of an existing `env` block), not just both added."""
        repo, settings_file = settings_repo
        stage_settings(
            repo,
            settings_file,
            '{"model": "sonnet", "effortLevel": "normal",'
            ' "env": {"CLAUDE_CODE_EFFORT_LEVEL": "high", "ANTHROPIC_MODEL": "opus"}}\n',
        )
        subprocess.run(
            ["git", "commit", "-am", "baseline with both nested keys"],
            cwd=repo, check=True, capture_output=True,
        )
        stage_settings(
            repo,
            settings_file,
            '{"model": "sonnet", "effortLevel": "normal",'
            ' "env": {"CLAUDE_CODE_EFFORT_LEVEL": "low", "ANTHROPIC_MODEL": "sonnet"}}\n',
        )
        assert (
            run_hook(
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
                bash_input("git commit -m 'change both nested keys'"),
                cwd=repo,
            )
            == "deny"
        )

    def test_nested_effort_level_null_against_leaf_absent_denies(self, settings_repo):
        """Nested mirror of test_guarded_key_set_to_null_against_absent_denies:
        an explicit null leaf must not read as equal to the leaf being absent
        from an `env` object that otherwise exists."""
        repo, settings_file = settings_repo
        stage_settings(
            repo, settings_file,
            '{"model": "sonnet", "effortLevel": "normal", "env": {}}\n',
        )
        subprocess.run(
            ["git", "commit", "-am", "baseline with empty env"],
            cwd=repo, check=True, capture_output=True,
        )
        stage_settings(
            repo,
            settings_file,
            '{"model": "sonnet", "effortLevel": "normal",'
            ' "env": {"CLAUDE_CODE_EFFORT_LEVEL": null}}\n',
        )
        assert (
            run_hook(
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
                bash_input("git commit -m 'explicit null leaf'"),
                cwd=repo,
            )
            == "deny"
        )

    def test_nested_effort_level_false_against_leaf_absent_denies(self, settings_repo):
        """Nested mirror of test_guarded_key_set_to_false_against_absent_denies:
        completes the false/null-vs-absent parity the guarded_value header
        comment claims holds at every path depth, not just the top level."""
        repo, settings_file = settings_repo
        stage_settings(
            repo, settings_file,
            '{"model": "sonnet", "effortLevel": "normal", "env": {}}\n',
        )
        subprocess.run(
            ["git", "commit", "-am", "baseline with empty env"],
            cwd=repo, check=True, capture_output=True,
        )
        stage_settings(
            repo,
            settings_file,
            '{"model": "sonnet", "effortLevel": "normal",'
            ' "env": {"CLAUDE_CODE_EFFORT_LEVEL": false}}\n',
        )
        assert (
            run_hook(
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
                bash_input("git commit -m 'explicit false leaf'"),
                cwd=repo,
            )
            == "deny"
        )

    def test_env_staged_as_non_object_against_real_value_denies(self, settings_repo):
        """A corrupted `env` (staged as a non-object) must fall through to
        "leaf absent" rather than erroring the whole jq program — and must
        still deny here, since main's side has a real guarded value."""
        repo, settings_file = settings_repo
        stage_settings(
            repo,
            settings_file,
            '{"model": "sonnet", "effortLevel": "normal",'
            ' "env": {"CLAUDE_CODE_EFFORT_LEVEL": "high"}}\n',
        )
        subprocess.run(
            ["git", "commit", "-am", "baseline with real nested value"],
            cwd=repo, check=True, capture_output=True,
        )
        stage_settings(
            repo,
            settings_file,
            '{"model": "sonnet", "effortLevel": "normal", "env": "corrupted"}\n',
        )
        assert (
            run_hook(
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
                bash_input("git commit -m 'corrupt env'"),
                cwd=repo,
            )
            == "deny"
        )

    def test_env_on_main_as_non_object_against_real_staged_value_denies(self, settings_repo):
        """Mirror of the above with the sides swapped: a corrupted `env` on
        main must also fall through to "leaf absent" rather than erroring —
        and must still deny here, since the staged side has a real guarded
        value."""
        repo, settings_file = settings_repo
        stage_settings(
            repo,
            settings_file,
            '{"model": "sonnet", "effortLevel": "normal", "env": "corrupted"}\n',
        )
        subprocess.run(
            ["git", "commit", "-am", "baseline with corrupted env"],
            cwd=repo, check=True, capture_output=True,
        )
        stage_settings(
            repo,
            settings_file,
            '{"model": "sonnet", "effortLevel": "normal",'
            ' "env": {"CLAUDE_CODE_EFFORT_LEVEL": "high"}}\n',
        )
        assert (
            run_hook(
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
                bash_input("git commit -m 'stage real env value'"),
                cwd=repo,
            )
            == "deny"
        )

    def test_unrelated_nested_env_key_change_allows_existing_env_object(self, settings_repo):
        """Must not over-guard the whole `env` block — only the two named
        leaves. Shape: `env` already exists on main."""
        repo, settings_file = settings_repo
        stage_settings(
            repo, settings_file,
            '{"model": "sonnet", "effortLevel": "normal", "env": {}}\n',
        )
        subprocess.run(
            ["git", "commit", "-am", "baseline with empty env"],
            cwd=repo, check=True, capture_output=True,
        )
        stage_settings(
            repo,
            settings_file,
            '{"model": "sonnet", "effortLevel": "normal",'
            ' "env": {"SOME_OTHER_VAR": "value"}}\n',
        )
        assert (
            run_hook(
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
                bash_input("git commit -m 'add unrelated env var'"),
                cwd=repo,
            )
            == "allow"
        )

    def test_unrelated_nested_env_key_change_allows_fresh_env_object(self, settings_repo):
        """Companion to the above: `env` does not exist on main at all, and
        the staged commit creates it solely for an unrelated var."""
        repo, settings_file = settings_repo
        stage_settings(
            repo,
            settings_file,
            '{"model": "sonnet", "effortLevel": "normal",'
            ' "env": {"SOME_OTHER_VAR": "value"}}\n',
        )
        assert (
            run_hook(
                GUARD_SETTINGS_SESSION_KEYS_HOOK,
                bash_input("git commit -m 'add unrelated env var'"),
                cwd=repo,
            )
            == "allow"
        )
