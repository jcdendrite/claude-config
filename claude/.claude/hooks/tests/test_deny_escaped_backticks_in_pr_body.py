"""Tests for deny-escaped-backticks-in-pr-body.sh.

The hook blocks `gh pr create` and `gh pr edit` commands whose PR
body contains literal backslash-backtick sequences. It fails closed
on pseudo-file paths and unreadable body-source files.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess

import pytest
from helpers import (
    HOOKS_DIR,
    assert_cap_engaged,
    bash_input,
    build_path_without,
    run_hook,
    run_hook_reason,
)

from .conftest import _real_timeout_is_gnu_coreutils, _write_conditional_sleep_shim

DENY_ESCAPED_BACKTICKS_HOOK = HOOKS_DIR / "deny-escaped-backticks-in-pr-body.sh"

# About 320 KB in 32 lines. It must be multi-line and exceed 100 KiB, the size
# at which the pipe form misses on every run (see
# .claude/plans/stow-reminder-sigpipe-fix.md row 5), not just pipe capacity:
# a pipe into `grep -q` returns 141 under pipefail once grep exits on a
# first-line match before the writer finishes.
LARGE_MULTILINE_TAIL = "\n" + "\n".join(["x" * 10_000] * 32)
assert LARGE_MULTILINE_TAIL.count("\n") > 1 and len(LARGE_MULTILINE_TAIL) > 100 * 1024


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

    def test_large_multiline_body_file_with_escaped_backtick_on_first_line_is_denied(self, tmp_path):
        """The escaped-backtick scan must see a match on the first line of a large
        multi-line body file."""
        body_file = tmp_path / "body.md"
        body_file.write_text("Use `\\`grep\\`` to search." + LARGE_MULTILINE_TAIL)
        cmd = f"gh pr create --body-file {body_file}"
        reason = run_hook_reason(DENY_ESCAPED_BACKTICKS_HOOK, bash_input(cmd))
        assert reason is not None
        assert "backslash-backtick" in reason

    def test_clean_body_file_is_allowed(self, tmp_path):
        body_file = tmp_path / "body.md"
        body_file.write_text("## Summary\n\nUse `grep` to search.\n")
        cmd = f"gh pr create --body-file {body_file}"
        assert run_hook(DENY_ESCAPED_BACKTICKS_HOOK, bash_input(cmd)) == "allow"

    def test_body_file_pseudo_path_is_denied_fail_closed(self):
        cmd = "gh pr create --body-file /dev/stdin"
        reason = run_hook_reason(DENY_ESCAPED_BACKTICKS_HOOK, bash_input(cmd))
        assert reason is not None
        assert "pseudo-file path" in reason

    def test_missing_body_file_is_denied_fail_closed(self):
        cmd = "gh pr create --body-file /nonexistent/path.md"
        assert run_hook(DENY_ESCAPED_BACKTICKS_HOOK, bash_input(cmd)) == "deny"

    def test_body_file_device_file_is_denied_not_hung(self, tmp_path):
        """`--body-file /dev/zero` must deny without `cat` ever touching it:
        /dev/zero passes the `[ -r ]` readability check but is not a regular
        file, so the `[ -f ]` guard rejects it before the capped read. A `cat`
        shim records any invocation on the device and never reads it, so
        removing the guard fails the test (rather than hanging or being masked
        by the cat-kill deny)."""
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
        env = {"PATH": f"{stub_dir}:{os.environ['PATH']}"}
        cmd = "gh pr create --body-file /dev/zero"
        assert run_hook(DENY_ESCAPED_BACKTICKS_HOOK, bash_input(cmd), extra_env=env) == "deny"
        assert not invocation_log.exists(), "cat was invoked on the non-regular body source"

    def _clean_body_file_and_command(self, tmp_path):
        body_file = tmp_path / "body.md"
        body_file.write_text("clean body, no escapes\n")
        return body_file, f"gh pr create --body-file {body_file}"

    @pytest.mark.timing
    def test_body_file_cat_timeout_is_denied_fail_closed(self, tmp_path):
        """A `cat` of the body file killed by the 5s cap (exit 124) denies
        rather than allowing on unscanned content."""
        body_file, cmd = self._clean_body_file_and_command(tmp_path)

        real_cat = shutil.which("cat")
        if not real_cat:
            pytest.skip("cat not found in PATH")
        if not shutil.which("timeout") and not shutil.which("gtimeout"):
            pytest.skip("neither timeout(1) nor gtimeout(1) available — BSD/macOS without coreutils")

        stub_dir = tmp_path / "stub-bin-cat"
        stub_dir.mkdir()
        _write_conditional_sleep_shim(stub_dir, "cat", real_cat, f'[ "$1" = {shlex.quote(str(body_file))} ]')

        env = {"PATH": f"{stub_dir}:{os.environ['PATH']}"}
        with assert_cap_engaged(stub_dir, production_cap=5, command="cat"):
            reason = run_hook_reason(DENY_ESCAPED_BACKTICKS_HOOK, bash_input(cmd), extra_env=env)
        assert reason is not None and "killed (exit 124)" in reason, reason

    @pytest.mark.timing
    def test_body_file_cat_sigterm_immune_kill_is_denied(self, tmp_path):
        """A `cat` that ignores SIGTERM is SIGKILLed by the cap's `-k` grace
        (exit 137, not 124); that status denies too."""
        body_file, cmd = self._clean_body_file_and_command(tmp_path)

        real_cat = shutil.which("cat")
        if not real_cat:
            pytest.skip("cat not found in PATH")
        if not _real_timeout_is_gnu_coreutils():
            pytest.skip("scaled sub-second -k grace is truncated to 0 by a non-GNU timeout, so SIGKILL never fires")

        stub_dir = tmp_path / "stub-bin-cat"
        stub_dir.mkdir()
        _write_conditional_sleep_shim(
            stub_dir, "cat", real_cat, f'[ "$1" = {shlex.quote(str(body_file))} ]', sigterm_immune=True
        )

        env = {"PATH": f"{stub_dir}:{os.environ['PATH']}"}
        reason = run_hook_reason(DENY_ESCAPED_BACKTICKS_HOOK, bash_input(cmd), extra_env=env)
        assert reason is not None and "killed (exit 137)" in reason, reason

    def test_body_file_cat_nonzero_exit_is_denied(self, tmp_path):
        """A `cat` of the body file that exits 1 (a read error after the
        readability check) denies rather than allowing on empty content."""
        body_file, cmd = self._clean_body_file_and_command(tmp_path)
        real_cat = shutil.which("cat")
        if not real_cat:
            pytest.skip("cat not found in PATH")
        stub_dir = tmp_path / "stub-bin-cat"
        stub_dir.mkdir()
        _write_conditional_sleep_shim(
            stub_dir, "cat", real_cat, f'[ "$1" = {shlex.quote(str(body_file))} ]', exit_status=1
        )
        env = {"PATH": f"{stub_dir}:{os.environ['PATH']}"}
        reason = run_hook_reason(DENY_ESCAPED_BACKTICKS_HOOK, bash_input(cmd), extra_env=env)
        assert reason is not None and "(exit 1)" in reason, reason
        assert "killed" not in reason, reason

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
