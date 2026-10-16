"""Tests for deny-escaped-backticks-in-pr-body.sh.

The hook blocks `gh pr create` and `gh pr edit` commands whose PR
body contains literal backslash-backtick sequences. It fails closed
on pseudo-file paths, unreadable body-source files, and a command that neither xargs nor the
fallback tokenizer can split.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess

import pytest
from helpers import (
    HEREDOC_BODY_WITH_GAP_APOSTROPHE,
    HOOKS_DIR,
    LARGE_MULTILINE_TAIL,
    assert_cap_engaged,
    bash_input,
    build_path_with_silent_python3,
    build_path_without,
    heredoc_substitution,
    run_hook,
    run_hook_reason,
)

from .conftest import _real_timeout_is_gnu_coreutils, _write_conditional_sleep_shim

DENY_ESCAPED_BACKTICKS_HOOK = HOOKS_DIR / "deny-escaped-backticks-in-pr-body.sh"

# A read-only gh pr subcommand carrying a multi-line quoted --jq value: xargs fails on it, bash accepts it.
GH_PR_VIEW_XARGS_FAILING_COMMAND = 'gh pr view 12 --json title --jq ".title\n| ascii_downcase"'


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

    # ------------------------------------------------------------------ #
    # Commands xargs cannot tokenize                                      #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        "command_template",
        [
            "gh pr create --title {substitution} --body-file {body_file}",
            "git commit -m {substitution} && gh pr create --body-file {body_file}",
        ],
        ids=["flag-after-heredoc-value", "chained-after-heredoc-commit"],
    )
    def test_body_file_after_xargs_failure_is_scanned(self, tmp_path, command_template):
        """A body file named after a value xargs cannot tokenize is still read and scanned."""
        body_file = tmp_path / "body.md"
        body_file.write_text("## Summary\n\nUse `\\`grep\\`` to search.\n")
        command = command_template.format(
            substitution=heredoc_substitution(HEREDOC_BODY_WITH_GAP_APOSTROPHE),
            body_file=body_file,
        )
        reason = run_hook_reason(DENY_ESCAPED_BACKTICKS_HOOK, bash_input(command))
        assert reason is not None
        assert "literal backslash-backtick sequences" in reason

    def test_untokenizable_pr_command_denied(self, tmp_path):
        """An unterminated quote defeats xargs and the fallback tokenizer, so the gate denies
        instead of skipping the --body-file that follows."""
        body_file = tmp_path / "body.md"
        body_file.write_text("clean body, no escapes\n")
        command = f'gh pr create --title "unterminated --body-file {body_file}'
        reason = run_hook_reason(DENY_ESCAPED_BACKTICKS_HOOK, bash_input(command))
        assert reason is not None
        assert "The gh pr command could not be tokenized" in reason

    def test_silent_python3_denies_xargs_failing_pr_command(self, tmp_path):
        """A python3 that exits 0 with no output is denied as a tokenizer that found no words,
        not allowed as a command naming no body files."""
        body_file = tmp_path / "body.md"
        body_file.write_text("clean body, no escapes\n")
        shim_dir = tmp_path / "silent-python3"
        shim_dir.mkdir()
        command_text_marker = "distinctive-marker-text"
        command = f'gh pr create --title "{command_text_marker}\nsecond line" --body-file {body_file}'
        reason = run_hook_reason(
            DENY_ESCAPED_BACKTICKS_HOOK,
            bash_input(command),
            extra_env={"PATH": build_path_with_silent_python3(shim_dir)},
        )
        assert reason is not None
        assert "The gh pr command could not be checked" in reason
        assert "found no command words to check" in reason
        assert "a tool in the extraction step failed" not in reason
        assert command_text_marker not in reason
        assert str(body_file) not in reason

    def test_gh_pr_view_xargs_failing_command_makes_plain_xargs_fail(self):
        """Precondition for the python3-absent allow case below: the command must defeat plain
        xargs, or that case would pass without ever reaching the tokenization skip."""
        if shutil.which("xargs") is None:
            pytest.skip("xargs not on PATH")
        result = subprocess.run(
            ["xargs", "-n1"],
            input=GH_PR_VIEW_XARGS_FAILING_COMMAND + "\n",
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0

    def test_gh_pr_view_with_xargs_failing_value_allowed_without_python3(self, tmp_path):
        """Scope guard: tokenization runs only for gh pr create/edit, so a gh pr subcommand that
        is neither is allowed even when a quoted value defeats xargs and python3 is absent."""
        command = GH_PR_VIEW_XARGS_FAILING_COMMAND
        farm_dir = tmp_path / "path-without-python3"
        farm_dir.mkdir()
        decision = run_hook(
            DENY_ESCAPED_BACKTICKS_HOOK,
            bash_input(command),
            extra_env={"PATH": build_path_without("python3", farm_dir)},
        )
        assert decision == "allow"

    def test_untokenizable_non_pr_command_allowed(self):
        """Scope guard: tokenization runs only for a gh pr create/edit command, so an unbalanced
        quote in an unrelated command is not this gate's concern."""
        assert run_hook(DENY_ESCAPED_BACKTICKS_HOOK, bash_input('echo "unterminated')) == "allow"

    def test_heredoc_pr_body_with_quotes_and_apostrophes_allowed(self):
        """Over-deny guard: the dominant heredoc shape with a clean body passes through the
        fallback tokenizer, whose heredoc-body strip is what keeps the apostrophe harmless."""
        command = f"gh pr create --title Tidy --body {heredoc_substitution(HEREDOC_BODY_WITH_GAP_APOSTROPHE)}"
        assert run_hook(DENY_ESCAPED_BACKTICKS_HOOK, bash_input(command)) == "allow"

    @pytest.mark.parametrize(
        "heredoc_body",
        ["Plain body.", "It's a plain body."],
        ids=["without-apostrophe", "with-apostrophe"],
    )
    def test_create_then_pass_body_file_denied_with_and_without_apostrophe(self, tmp_path, heredoc_body):
        """A command that writes a new body file and passes it in the same call denies in both
        arms: the file does not exist yet at hook time, whatever the heredoc body holds."""
        body_file = tmp_path / "created-body.md"
        command = f"cat > {body_file} <<'EOF'\n{heredoc_body}\nEOF\ngh pr create --body-file {body_file}"
        reason = run_hook_reason(DENY_ESCAPED_BACKTICKS_HOOK, bash_input(command))
        assert reason is not None
        assert "references a body-source file at" in reason
        assert str(body_file) in reason

    def test_tokenizer_file_not_linked_denies(self, tmp_path):
        """With the fallback tokenizer missing from the hooks directory, a command xargs cannot
        split denies and names ./install.sh; a command xargs can split still passes."""
        hooks_copy = tmp_path / "hooks-copy"
        hooks_copy.mkdir()
        for name in (DENY_ESCAPED_BACKTICKS_HOOK.name, "_lib.sh", "_config.sh", "config-keys.psv"):
            shutil.copy(HOOKS_DIR / name, hooks_copy / name)
        hook_copy = hooks_copy / DENY_ESCAPED_BACKTICKS_HOOK.name

        multiline_command = (
            f"gh pr create --title Tidy --body {heredoc_substitution(HEREDOC_BODY_WITH_GAP_APOSTROPHE)}"
        )
        reason = run_hook_reason(hook_copy, bash_input(multiline_command))
        assert reason is not None
        assert "not linked into the hooks directory" in reason
        assert "./install.sh" in reason
        assert "Write multi-line" not in reason

        single_line_command = "gh pr create --title Tidy --body 'clean body no escapes'"
        assert run_hook(hook_copy, bash_input(single_line_command)) == "allow"
