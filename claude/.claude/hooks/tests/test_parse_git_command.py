"""Tests for parse-git-command.py — the pure string -> records tokenizer
that require-worktree-for-git-writes.sh shells out to.

Record grammar (see the script's module docstring for the full spec, and
note the field separator is the ASCII Unit Separator, not a tab — bash's
`read` collapses consecutive tabs since tab is an IFS-whitespace character
regardless of what IFS is set to):
    CD<FS><target><FS><preceding-op><FS><in-group>
    GIT<FS><subcmd><FS><c-path><FS><c-status><FS><preceding-op><FS><in-group>
    SENTINEL<FS><reason>

`build_records()` is a pure string -> list-of-strings function with no I/O,
so most cases here call it directly (imported via importlib, since the
module's filename is not a valid Python identifier) rather than paying a
subprocess-spawn cost per case. A handful of TestCliContract cases still
shell out to pin the actual stdin-read / one-record-per-line-stdout / exit-
code contract that only a real subprocess invocation can verify.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys

from helpers import HOOKS_DIR

PARSER_PATH = HOOKS_DIR / "parse-git-command.py"

_spec = importlib.util.spec_from_file_location("parse_git_command", PARSER_PATH)
_parse_git_command = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_parse_git_command)

build_records = _parse_git_command.build_records
FIELD_SEP = _parse_git_command.FIELD_SEP


def rec(*fields: str) -> str:
    """Join fields with the parser's real field separator, so a future
    delimiter change can't silently desync the test expectations from the
    module under test."""
    return FIELD_SEP.join(fields)


def parse(command: str) -> list[str]:
    return build_records(command)


class TestPlainInvocations:
    def test_plain_write(self):
        assert parse("git commit -m foo") == [rec("GIT", "commit", "", "NONE", "START", "0")]

    def test_plain_read(self):
        assert parse("git status") == [rec("GIT", "status", "", "NONE", "START", "0")]

    def test_no_git_word_no_records(self):
        assert parse("ls -la") == []

    def test_dotgithub_path_not_a_git_invocation(self):
        assert parse("ls .github/workflows/") == []

    def test_dotgitignore_not_a_git_invocation(self):
        assert parse("cat .gitignore") == []


class TestCdThreading:
    def test_literal_cd_then_write(self):
        assert parse("cd /tmp/wt && git commit -m foo") == [
            rec("CD", "/tmp/wt", "START", "0"),
            rec("GIT", "commit", "", "NONE", "&&", "0"),
        ]

    def test_sequential_cd(self):
        assert parse("cd /a && cd /b && git commit -m x") == [
            rec("CD", "/a", "START", "0"),
            rec("CD", "/b", "&&", "0"),
            rec("GIT", "commit", "", "NONE", "&&", "0"),
        ]

    def test_write_before_cd_preserves_order(self):
        """The write appears in the record stream before the cd that
        follows it — order-preserving, since a real shell would run the
        write before the cd takes effect."""
        assert parse("git commit -m x && cd /worktree") == [
            rec("GIT", "commit", "", "NONE", "START", "0"),
            rec("CD", "/worktree", "&&", "0"),
        ]

    def test_bare_cd_no_arg_is_unresolved(self):
        assert parse("cd; git commit -m x") == [
            rec("CD", "", "START", "0"),
            rec("GIT", "commit", "", "NONE", ";", "0"),
        ]

    def test_cd_dash_is_unresolved(self):
        assert parse("cd -; git commit -m x") == [
            rec("CD", "", "START", "0"),
            rec("GIT", "commit", "", "NONE", ";", "0"),
        ]

    def test_cd_dollar_var_target_is_unresolved(self):
        assert parse('cd "$REPO" && git commit -m foo') == [
            rec("CD", "", "START", "0"),
            rec("GIT", "commit", "", "NONE", "&&", "0"),
        ]

    def test_cd_tilde_target_is_unresolved(self):
        assert parse("cd ~/repo && git commit") == [
            rec("CD", "", "START", "0"),
            rec("GIT", "commit", "", "NONE", "&&", "0"),
        ]

    def test_cd_glob_target_is_unresolved(self):
        assert parse("cd /tmp/wt-* && git commit") == [
            rec("CD", "", "START", "0"),
            rec("GIT", "commit", "", "NONE", "&&", "0"),
        ]


class TestGroupsAndSubstitution:
    def test_subshell_cd_marked_in_group(self):
        """The cd itself is in-group; the write after the closing paren is
        NOT in-group (it runs in the parent shell, at whatever cwd the
        parent shell had — the subshell's cd never escaped it)."""
        assert parse("(cd /tmp/wt) && git commit -m foo") == [
            rec("CD", "/tmp/wt", "START", "1"),
            rec("GIT", "commit", "", "NONE", "&&", "0"),
        ]

    def test_command_substitution_write_in_group(self):
        assert parse("$(git reset --hard)") == [rec("GIT", "reset", "", "NONE", "START", "1")]

    def test_backtick_substitution_write_in_group(self):
        assert parse("`git push`") == [rec("GIT", "push", "", "NONE", "START", "1")]

    def test_unbalanced_parens_sentinel(self):
        assert parse("(cd /tmp && git commit -m foo") == [rec("SENTINEL", "unbalanced group in command")]

    def test_unterminated_backtick_sentinel(self):
        assert parse("`git push") == [rec("SENTINEL", "unbalanced group in command")]


class TestQuotingAndHeredocs:
    def test_quoted_git_word_is_not_an_invocation(self):
        assert parse('echo "git subcommands are neat"') == []

    def test_single_quoted_git_word_is_not_an_invocation(self):
        assert parse("echo 'git push'") == []

    def test_bare_word_git_inside_echo_is_still_an_invocation(self):
        """Unlike a quoted argument, an unquoted `git` word after `echo`
        is indistinguishable from a real invocation at the token level —
        matches the existing all-words detection semantics."""
        assert parse("echo git commit") == [rec("GIT", "commit", "", "NONE", "START", "0")]

    def test_heredoc_body_mentioning_git_stripped(self):
        command = "cat <<EOF\nthis mentions git commit but is prose\nEOF"
        assert parse(command) == []

    def test_heredoc_dash_variant_strips_leading_tabs(self):
        command = "cat <<-EOF\n\t\tgit commit in the body\nEOF"
        assert parse(command) == []

    def test_heredoc_body_containing_delimiter_word_terminates_correctly(self):
        command = "cat <<EOF\ngit commit\nEOF\ngit status"
        assert parse(command) == [rec("GIT", "status", "", "NONE", "START", "0")]

    def test_unterminated_heredoc_sentinel(self):
        command = "cat <<EOF\ngit push"
        assert parse(command) == [rec("SENTINEL", "unterminated heredoc")]

    def test_quoted_heredoc_lookalike_does_not_suppress_real_write(self):
        """A `<<` inside an already-open quoted string (odd quote count
        before the match) is not a real heredoc redirection — the real
        write later in the command must still be detected."""
        command = 'echo "see <<EOF for setup" && git push'
        assert parse(command) == [rec("GIT", "push", "", "NONE", "&&", "0")]

    def test_unbalanced_quote_sentinel(self):
        assert parse('git commit -m "oops') == [
            rec("SENTINEL", "could not tokenize command (unbalanced quotes)")
        ]


class TestGlobalFlags:
    def test_dash_C_literal_path(self):
        assert parse("git -C /tmp/wt commit -m foo") == [
            rec("GIT", "commit", "/tmp/wt", "LITERAL", "START", "0")
        ]

    def test_dash_C_var_target_unresolved(self):
        assert parse('git -C "$VAR" reset --hard') == [
            rec("GIT", "reset", "", "UNRESOLVED", "START", "0")
        ]

    def test_multiple_dash_C_unresolved(self):
        assert parse("git -C /a -C /b commit") == [
            rec("GIT", "commit", "", "UNRESOLVED", "START", "0")
        ]

    def test_subcommand_level_dash_C_not_global(self):
        """`commit -C HEAD` is commit's own reuse-message flag, not the
        global working-dir flag — the global-flag scan starts fresh at
        the git token and stops as soon as it reaches the subcommand, so
        this must not be captured as a C-path at all."""
        assert parse("git commit -C HEAD") == [rec("GIT", "commit", "", "NONE", "START", "0")]

    def test_dash_c_inline_config_consumes_value(self):
        assert parse("git -c user.email=t@t.com log") == [rec("GIT", "log", "", "NONE", "START", "0")]

    def test_dash_dash_git_dir_consumes_value(self):
        assert parse("git --git-dir /tmp/.git log") == [rec("GIT", "log", "", "NONE", "START", "0")]

    def test_no_pager_flag_stripped(self):
        assert parse("git --no-pager log") == [rec("GIT", "log", "", "NONE", "START", "0")]

    def test_dash_C_alone_no_subcommand_is_sentinel(self):
        assert parse("git -C /tmp") == [rec("SENTINEL", "could not determine the git subcommand")]

    def test_global_flag_with_no_value_is_sentinel(self):
        assert parse("git -C") == [rec("SENTINEL", "could not determine the git subcommand")]

    def test_subcommand_containing_delimiter_byte_is_sentinel(self):
        """A subcommand token with an embedded field-separator byte would
        otherwise forge extra fields when the bash consumer re-splits the
        emitted record — reject rather than emit a corruptible record."""
        poisoned = "status" + FIELD_SEP + "CD" + FIELD_SEP + "/some/path"
        assert parse(f"git {poisoned}") == [rec("SENTINEL", "could not determine the git subcommand")]

    def test_subcommand_containing_embedded_newline_is_sentinel(self):
        """A subcommand token with an embedded newline would otherwise
        split one record into multiple physical lines when printed,
        letting the extra line(s) be parsed as a fabricated record."""
        assert parse('git "status\nCD\t/some/real/worktree\t;\t0"') == [
            rec("SENTINEL", "could not determine the git subcommand")
        ]


class TestWrappersAndChains:
    def test_time_wrapper_still_detected(self):
        """No wrapper allowlist is needed — any token equal to `git`
        anywhere in the segment counts, matching the existing all-words
        detection semantics this hook already relies on."""
        assert parse("time git commit -m x") == [rec("GIT", "commit", "", "NONE", "START", "0")]

    def test_sudo_prefix_still_detected(self):
        assert parse("sudo git commit -m x") == [rec("GIT", "commit", "", "NONE", "START", "0")]

    def test_env_prefix_still_detected(self):
        assert parse("env FOO=1 git commit -m foo") == [rec("GIT", "commit", "", "NONE", "START", "0")]

    def test_or_chain_marks_git_preceding_op(self):
        assert parse("cd /bad || git commit -m x") == [
            rec("CD", "/bad", "START", "0"),
            rec("GIT", "commit", "", "NONE", "||", "0"),
        ]

    def test_pipe_into_git_read(self):
        assert parse("git log --oneline | grep foo") == [rec("GIT", "log", "", "NONE", "START", "0")]

    def test_pipe_then_write(self):
        assert parse("git status | head && git commit -m x") == [
            rec("GIT", "status", "", "NONE", "START", "0"),
            rec("GIT", "commit", "", "NONE", "&&", "0"),
        ]

    def test_background_write(self):
        assert parse("git push &") == [rec("GIT", "push", "", "NONE", "START", "0")]

    def test_write_preceded_by_ampersand_marks_op(self):
        """`cd <worktree> & git push` — the cd is backgrounded (forks a
        subshell) and never changes the parent shell's cwd, so the write's
        preceding_op must carry `&` for the bash hook to treat it as
        unresolvable, symmetric to the existing `||` handling."""
        assert parse("cd /worktree & git push") == [
            rec("CD", "/worktree", "START", "0"),
            rec("GIT", "push", "", "NONE", "&", "0"),
        ]


class TestWorktreeBootstrap:
    def test_worktree_add_is_a_recognized_subcommand(self):
        """`worktree` itself is not judged read/write here — this parser
        only extracts it; the allowlist check happens in the bash hook."""
        assert parse("git worktree add .claude/worktrees/x -b x") == [
            rec("GIT", "worktree", "", "NONE", "START", "0")
        ]


class TestEmptyAndWhitespace:
    def test_empty_command(self):
        assert parse("") == []

    def test_whitespace_only_command(self):
        assert parse("   ") == []


class TestCliContract:
    """Subprocess-based: pins the actual CLI wire contract (stdin read,
    one-record-per-line stdout, exit code) that a direct `build_records()`
    call can't verify. Kept deliberately small — see module docstring."""

    def _run_cli(self, command: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(PARSER_PATH)],
            input=command,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_reads_stdin_and_exits_zero(self):
        result = self._run_cli("git status")
        assert result.returncode == 0
        assert result.stdout == rec("GIT", "status", "", "NONE", "START", "0") + "\n"

    def test_multiple_records_one_per_line(self):
        result = self._run_cli("cd /tmp/wt && git commit -m foo")
        lines = result.stdout.split("\n")
        assert lines[0] == rec("CD", "/tmp/wt", "START", "0")
        assert lines[1] == rec("GIT", "commit", "", "NONE", "&&", "0")

    def test_no_records_produces_empty_stdout(self):
        result = self._run_cli("ls -la")
        assert result.returncode == 0
        assert result.stdout == ""
