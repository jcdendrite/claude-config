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
    build_path_without,
    run_hook,
    run_hook_reason,
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

    # ------------------------------------------------------------------ #
    # Quote-split and fail-closed status-2 regression                     #
    # ------------------------------------------------------------------ #

    def test_quoted_command_word_reaches_same_verdict_as_bare_form(self):
        """A quote-adjacent split (`"gh" pr create ...`) must reach the
        same deny verdict as the unquoted form — the gh-family matcher
        strips quote characters before word-walking, unlike a raw regex
        over unstripped $COMMAND."""
        cmd = "\"gh\" pr create --body 'body with \\`escaped\\` backticks'"
        assert run_hook(DENY_ESCAPED_BACKTICKS_HOOK, bash_input(cmd)) == "deny"

    def test_sed_absent_from_path_denies(self, tmp_path):
        """Status-2 propagation: the matcher could not determine whether
        this command invokes gh pr create/edit, and this gate's own
        documented fail-closed posture means an undetermined match denies
        rather than silently falling through to allow — even for a clean
        body with no backtick to trigger the content detector itself.
        Asserts the distinguishing reason text, not just the verdict, so
        this test cannot be satisfied by an ordinary backtick-match deny
        reaching "deny" for the wrong reason."""
        farm_dir = tmp_path / "path-without-sed"
        farm_dir.mkdir()
        restricted_path = build_path_without("sed", farm_dir)
        cmd = "gh pr create --body 'clean body no escapes'"
        reason = run_hook_reason(
            DENY_ESCAPED_BACKTICKS_HOOK,
            bash_input(cmd),
            extra_env={"PATH": restricted_path},
        )
        assert reason is not None
        assert "could not determine" in reason
