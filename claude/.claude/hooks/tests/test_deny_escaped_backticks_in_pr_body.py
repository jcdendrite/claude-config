"""Tests for deny-escaped-backticks-in-pr-body.sh.

The hook blocks `gh pr create` and `gh pr edit` commands whose PR
body contains literal backslash-backtick sequences. It fails closed
on pseudo-file paths and unreadable body-source files.
"""
from __future__ import annotations

import json
import subprocess

from helpers import (
    HOOKS_DIR,
    bash_input,
    run_hook,
)

DENY_ESCAPED_BACKTICKS_HOOK = HOOKS_DIR / "deny-escaped-backticks-in-pr-body.sh"


class TestDenyEscapedBackticksInPrBody:
    def test_non_pr_command_is_allowed(self):
        assert run_hook(DENY_ESCAPED_BACKTICKS_HOOK, bash_input("git status")) == "allow"

    def test_gh_pr_view_is_allowed(self):
        assert run_hook(DENY_ESCAPED_BACKTICKS_HOOK, bash_input("gh pr view 5")) == "allow"

    def test_clean_body_is_allowed(self):
        cmd = "gh pr create --body 'clean body no escapes'"
        assert run_hook(DENY_ESCAPED_BACKTICKS_HOOK, bash_input(cmd)) == "allow"

    def test_escaped_backtick_in_inline_body_is_denied(self):
        # The command string itself contains \` — this is the classic
        # heredoc-escape bug reproduced as an inline --body value.
        cmd = r"gh pr create --body 'body with \`escaped\` backticks'"
        assert run_hook(DENY_ESCAPED_BACKTICKS_HOOK, bash_input(cmd)) == "deny"

    def test_escaped_backtick_in_gh_pr_edit_is_denied(self):
        cmd = r"gh pr edit 42 --body 'title \`code\` here'"
        assert run_hook(DENY_ESCAPED_BACKTICKS_HOOK, bash_input(cmd)) == "deny"

    def test_escaped_backtick_in_body_file_is_denied(self, tmp_path):
        body_file = tmp_path / "body.md"
        body_file.write_text("## Summary\n\nUse `\\`grep\\`` to search.\n")
        cmd = f"gh pr create --body-file {body_file}"
        assert run_hook(DENY_ESCAPED_BACKTICKS_HOOK, bash_input(cmd)) == "deny"

    def test_clean_body_file_is_allowed(self, tmp_path):
        body_file = tmp_path / "body.md"
        body_file.write_text("## Summary\n\nUse `grep` to search.\n")
        cmd = f"gh pr create --body-file {body_file}"
        assert run_hook(DENY_ESCAPED_BACKTICKS_HOOK, bash_input(cmd)) == "allow"

    def test_body_file_pseudo_path_is_denied_fail_closed(self):
        cmd = "gh pr create --body-file /dev/stdin"
        assert run_hook(DENY_ESCAPED_BACKTICKS_HOOK, bash_input(cmd)) == "deny"

    def test_missing_body_file_is_denied_fail_closed(self):
        cmd = "gh pr create --body-file /nonexistent/path.md"
        assert run_hook(DENY_ESCAPED_BACKTICKS_HOOK, bash_input(cmd)) == "deny"

    def test_chained_command_with_escaped_backtick_is_denied(self):
        cmd = r"git status && gh pr edit 1 --body 'foo \`bar\`'"
        assert run_hook(DENY_ESCAPED_BACKTICKS_HOOK, bash_input(cmd)) == "deny"

    def test_legitimate_shell_example_with_escaped_backtick_is_denied(self):
        # Even a \` inside a fenced shell code block is caught — the fix
        # is always to drop the backslash, not to carve out code blocks.
        cmd = r"gh pr create --body '## Notes\n```sh\nfoo \`bar\`\n```'"
        assert run_hook(DENY_ESCAPED_BACKTICKS_HOOK, bash_input(cmd)) == "deny"

    def test_malformed_json_is_denied_fail_closed(self):
        result = subprocess.run(
            [str(DENY_ESCAPED_BACKTICKS_HOOK)],
            input=b"not json",
            capture_output=True,
            check=False,
        )
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
