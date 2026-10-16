"""Tests for split-command-words.py, the shlex-based fallback tokenizer behind
_lib.sh's _lib_command_words.

`split_command_words()` is a pure string -> list-of-words function, so most
cases call it directly (imported via importlib, since the module's filename is
not a valid Python identifier). The subprocess cases pin the stdin/stdout/exit
status contract that only a real invocation can verify.

Exit status contract: 0 success, 3 the input cannot be tokenized, 2 any other
failure. Status 1 is never used.

The parity cases assume GNU xargs. Each GNU-specific one asserts its xargs
precondition first, so a BSD xargs fails with a message naming the assumption
instead of a bare mismatch.
"""
from __future__ import annotations

import ast
import importlib.util
import io
import shutil
import subprocess
import sys

import pytest
from helpers import HOOKS_DIR

SPLITTER_PATH = HOOKS_DIR / "split-command-words.py"

GNU_XARGS_ASSUMPTION = "this case assumes GNU xargs behavior, which BSD xargs does not share"

_spec = importlib.util.spec_from_file_location("split_command_words", SPLITTER_PATH)
_split_command_words_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_split_command_words_module)

split_command_words = _split_command_words_module.split_command_words

MAX_STDIN_BYTES = _split_command_words_module.MAX_STDIN_BYTES


requires_xargs = pytest.mark.skipif(shutil.which("xargs") is None, reason="xargs not on PATH")


def run_splitter(stdin_bytes: bytes, script_path=SPLITTER_PATH) -> subprocess.CompletedProcess:
    """Run the script under the interpreter flags _lib.sh's _lib_command_words uses."""
    return subprocess.run(
        [sys.executable, "-I", "-S", str(script_path)],
        input=stdin_bytes,
        capture_output=True,
        check=False,
    )


def run_xargs(command: str) -> subprocess.CompletedProcess:
    return subprocess.run(["xargs", "-n1"], input=(command + "\n").encode(), capture_output=True, check=False)


def xargs_words(command: str) -> list[str]:
    """The words `xargs -n1` prints for `command`, as the fast path computes them."""
    result = run_xargs(command)
    assert result.returncode == 0, f"xargs could not split {command!r}"
    return result.stdout.decode().split("\n")[:-1]


class TestHeredocBodies:
    def test_multiline_double_quoted_value_flattens_to_one_word_before_next_flag(self):
        words = split_command_words('git commit -m "line one\nline two" -F /x')
        assert words == ["git", "commit", "-m", "line one line two", "-F", "/x"]

    def test_heredoc_in_substitution_with_odd_quotes_and_backticks_is_dropped(self):
        command = (
            "gh pr create --body \"$(cat <<'EOF'\n"
            "it's \"odd\" and \"unbalanced\n"
            "a `backtick` line, don't\n"
            "EOF\n"
            ')" --body-file /x'
        )
        words = split_command_words(command)
        assert words == ["gh", "pr", "create", "--body", "$(cat <<'EOF' )", "--body-file", "/x"]

    def test_two_heredoc_substitutions_chained_with_and(self):
        command = (
            "git commit -m \"$(cat <<'EOF'\nfirst it's\nEOF\n)\" && "
            "gh pr create --body \"$(cat <<'EOF'\nsecond \"odd\nEOF\n)\" --body-file /x"
        )
        words = split_command_words(command)
        assert words == [
            "git", "commit", "-m", "$(cat <<'EOF' )", "&&",
            "gh", "pr", "create", "--body", "$(cat <<'EOF' )", "--body-file", "/x",
        ]  # fmt: skip

    def test_tail_of_opener_line_survives(self):
        command = "cat <<EOF && gh pr create --body-file /x\nbody text it's\nEOF"
        words = split_command_words(command)
        assert words == ["cat", "<<EOF", "&&", "gh", "pr", "create", "--body-file", "/x"]

    def test_dash_heredoc_with_tab_indented_terminator(self):
        command = "cat <<-EOF\n\tbody -F /evil\n\tEOF\ngh pr create --body-file /x"
        words = split_command_words(command)
        assert "/evil" not in words
        assert words[-2:] == ["--body-file", "/x"]

    def test_plain_heredoc_does_not_strip_tabs_from_terminator(self):
        command = "cat <<EOF\nbody\n\tEOF\nEOF\ngh pr create --body-file /x"
        words = split_command_words(command)
        assert words == ["cat", "<<EOF", "gh", "pr", "create", "--body-file", "/x"]

    def test_backslash_prefixed_delimiter(self):
        words = split_command_words("cat <<\\EOF\nbody -F /evil\nEOF\ngh pr create --body-file /x")
        assert words == ["cat", "<<EOF", "gh", "pr", "create", "--body-file", "/x"]

    def test_double_quoted_delimiter(self):
        words = split_command_words('cat <<"EOF"\nbody -F /evil\nEOF\ngh pr create --body-file /x')
        assert words == ["cat", "<<EOF", "gh", "pr", "create", "--body-file", "/x"]

    def test_blank_between_opener_and_bare_delimiter(self):
        words = split_command_words("cat << EOF\nbody it's -F /evil\nEOF\ngh pr create --body-file /x")
        assert words == ["cat", "<<", "EOF", "gh", "pr", "create", "--body-file", "/x"]

    def test_blank_between_opener_and_quoted_delimiter(self):
        words = split_command_words("cat << 'EOF'\nbody it's -F /evil\nEOF\ngh pr create --body-file /x")
        assert words == ["cat", "<<", "EOF", "gh", "pr", "create", "--body-file", "/x"]

    def test_here_string_is_not_an_opener(self):
        words = split_command_words("cat <<<EOF\nkeep --body-file /x\nEOF")
        assert words == ["cat", "<<<EOF", "keep", "--body-file", "/x", "EOF"]

    def test_quoted_opener_mention_without_terminator_drops_nothing(self):
        words = split_command_words('echo "see <<EOF for setup"\ngh pr create --body-file /x')
        assert words == ["echo", "see <<EOF for setup", "gh", "pr", "create", "--body-file", "/x"]

    @pytest.mark.parametrize("separator", ["\u2028", "\x0c"], ids=["u2028", "form-feed"])
    def test_body_line_holding_a_non_lf_line_break_is_dropped_whole(self, separator):
        """str.splitlines() would split on these and end the body at the fake terminator."""
        command = f"cat <<'EOF'\nx{separator}EOF\n-F /evil\nEOF\ngh pr create --body-file /x"
        words = split_command_words(command)
        assert "/evil" not in words
        assert words[-2:] == ["--body-file", "/x"]

    def test_two_openers_on_one_line_drop_both_bodies(self):
        command = "cat <<A <<B\nfirst -F /evil1\nA\nsecond -F /evil2\nB\ngh pr create --body-file /x"
        words = split_command_words(command)
        assert words == ["cat", "<<A", "<<B", "gh", "pr", "create", "--body-file", "/x"]


class TestLineContinuationsAndComments:
    def test_backslash_newline_without_indentation_joins_lines(self):
        words = split_command_words("gh pr create \\\n--body-file /x")
        assert words == ["gh", "pr", "create", "--body-file", "/x"]

    def test_escaped_backslash_before_newline_is_not_a_continuation(self):
        words = split_command_words("echo a\\\\\nb")
        assert words == ["echo", "a\\", "b"]

    def test_comment_line_with_apostrophe_before_command(self):
        words = split_command_words("# it's the plan\ngh pr create --body-file /x")
        assert words == ["gh", "pr", "create", "--body-file", "/x"]

    def test_indented_comment_line_with_apostrophe_before_command(self):
        words = split_command_words("  \t# it's indented\ngh pr create --body-file /x")
        assert words == ["gh", "pr", "create", "--body-file", "/x"]

    def test_comment_line_ending_in_backslash_does_not_join_next_line(self):
        words = split_command_words("# note \\\ngh pr create --body-file /x")
        assert words == ["gh", "pr", "create", "--body-file", "/x"]

    def test_mid_word_hash_stays_in_its_word(self):
        words = split_command_words("git commit -m Fix#12 -F /x/msg")
        assert words == ["git", "commit", "-m", "Fix#12", "-F", "/x/msg"]

    def test_hash_inside_path_keeps_whole_path_and_following_flags(self):
        words = split_command_words("gh pr create --body-file /tmp/dir#1/body.md --repo r")
        assert words == ["gh", "pr", "create", "--body-file", "/tmp/dir#1/body.md", "--repo", "r"]

    def test_quoted_line_that_is_exactly_a_flag_never_equals_the_flag_word(self):
        words = split_command_words('gh pr create --body "intro\n-F\n/evil\nmore" --repo r')
        assert "-F" not in words
        assert words == ["gh", "pr", "create", "--body", "intro -F /evil more", "--repo", "r"]


class TestWordShapes:
    def test_single_line_word_of_200_kb_then_flag(self):
        words = split_command_words("echo " + "x" * 200_000 + " -F /x")
        assert words[-2:] == ["-F", "/x"]
        assert len(words[1]) == 200_000

    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            ("gh pr create --body-file=/x", ["gh", "pr", "create", "--body-file=/x"]),
            ("git commit -F=/x", ["git", "commit", "-F=/x"]),
            ("git commit -F '/path with space/msg'", ["git", "commit", "-F", "/path with space/msg"]),
        ],
    )
    @requires_xargs
    def test_equals_form_and_quoted_path_with_space(self, command, expected):
        assert split_command_words(command) == expected
        assert split_command_words(command) == xargs_words(command)

    def test_non_utf8_byte_in_path_round_trips_through_the_entry_point(self):
        result = run_splitter(b"git commit -F /x/\xff/msg\n")
        assert result.returncode == 0
        assert result.stdout == b"git\ncommit\n-F\n/x/\xff/msg\n"

    def test_valid_multibyte_utf8_round_trips_through_the_entry_point(self):
        command = "git commit -m “quoted”—dash -F /x/é.md"
        result = run_splitter(command.encode("utf-8"))
        assert result.returncode == 0
        assert result.stdout.decode("utf-8").split("\n")[:-1] == [
            "git", "commit", "-m", "“quoted”—dash", "-F", "/x/é.md",
        ]  # fmt: skip


class TestExitCodes:
    @pytest.mark.parametrize(
        "command",
        [
            "git commit -m it's -F /x",
            'gh pr create --title "$(echo "it\'s")" --body-file /x',
            "git commit -m $'it\\'s' -F /x",
            'gh pr create --title "unterminated --body-file /x',
        ],
        ids=["lone-apostrophe", "nested-quote-in-substitution", "dollar-quote-escaped-apostrophe",
             "unterminated-double-quote"],
    )  # fmt: skip
    def test_untokenizable_input_exits_3_with_empty_stdout(self, command):
        result = run_splitter(command.encode())
        assert result.returncode == 3
        assert result.stdout == b""

    def test_trailing_backslash_with_the_callers_final_newline_is_a_continuation(self):
        """_lib_command_words feeds the command through a here-string, which always appends a newline."""
        result = run_splitter(b"echo trailing\\\n")
        assert result.returncode == 0
        assert result.stdout == b"echo\ntrailing\n"

    def test_stdin_over_the_size_cap_exits_3_with_empty_stdout(self):
        result = run_splitter(b"a " * (MAX_STDIN_BYTES // 2 + 1))
        assert result.returncode == 3
        assert result.stdout == b""

    def test_stdin_exactly_at_the_size_cap_is_still_tokenized(self):
        """A comment line is dropped before tokenizing, which keeps this boundary case fast:
        shlex takes quadratic time on one multi-hundred-KB word."""
        result = run_splitter(b"#" * MAX_STDIN_BYTES)
        assert result.returncode == 0
        assert result.stdout == b""

    def test_internal_exception_exits_2_never_1(self, monkeypatch):
        def raise_runtime_error(_command):
            raise RuntimeError("forced")

        monkeypatch.setattr(_split_command_words_module, "split_command_words", raise_runtime_error)
        stderr_buffer = io.BytesIO()
        self._patch_standard_streams(monkeypatch, stdout_buffer=io.BytesIO(), stderr_buffer=stderr_buffer)
        assert _split_command_words_module.main() == 2
        assert "RuntimeError" in stderr_buffer.getvalue().decode()

    def test_value_error_from_the_stdout_write_exits_2_not_3(self, monkeypatch):
        """Only a tokenizer rejection is a property of the command; a write fault is a tool fault."""
        stdout_buffer = io.BytesIO()
        stderr_buffer = io.BytesIO()
        self._patch_standard_streams(monkeypatch, stdout_buffer=stdout_buffer, stderr_buffer=stderr_buffer)
        stdout_buffer.close()
        assert _split_command_words_module.main() == 2
        assert "ValueError" in stderr_buffer.getvalue().decode()

    @staticmethod
    def _patch_standard_streams(monkeypatch, stdout_buffer, stderr_buffer):
        """Replace sys.stdin/stdout/stderr with wrappers this test owns, in place of capsys.

        Patching sys.stdout alongside capsys makes monkeypatch's teardown restore capsys's
        already-closed stream, which fails pytest's own final flush under `-s`.
        """
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"echo x")))
        monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(stdout_buffer))
        monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(stderr_buffer, write_through=True))

    def test_empty_stdin_exits_0_with_no_words(self):
        result = run_splitter(b"")
        assert result.returncode == 0
        assert result.stdout == b""


@requires_xargs
class TestParityWithXargs:
    @pytest.mark.parametrize(
        "command",
        [
            "git commit -m 'a b' --file=/x -F '/p q/r'",
            'git commit -m "a b" -F=/x',
            "a\\ b c",
            'a "" b',
            "gh pr create --body-file /x --repo owner/name",
            "git commit -m 'it\"s' -F /x",
        ],
    )
    def test_commands_xargs_can_split_yield_the_same_words(self, command):
        assert split_command_words(command) == xargs_words(command)

    def test_intended_divergence_backslash_newline_joins_where_xargs_splits(self):
        command = "a\\\nb"
        assert xargs_words(command) == ["a", "b"], (
            f"precondition: {GNU_XARGS_ASSUMPTION}; xargs should split at the newline"
        )
        assert split_command_words(command) == ["ab"]

    def test_intended_divergence_escaped_double_quote_is_honored_where_xargs_fails(self):
        command = 'echo "say \\"hi\\" now" x'
        assert run_xargs(command).returncode != 0, (
            f"precondition: {GNU_XARGS_ASSUMPTION}; xargs should reject an escaped quote"
        )
        assert split_command_words(command) == ["echo", 'say "hi" now', "x"]

    @pytest.mark.parametrize("flag_like_word", ["-e", "-n", "-E"])
    def test_intended_divergence_echo_option_words_are_kept(self, flag_like_word):
        command = f"foo {flag_like_word} bar"
        assert flag_like_word not in xargs_words(command), (
            f"precondition: {GNU_XARGS_ASSUMPTION}; xargs's echo should swallow {flag_like_word}"
        )
        assert split_command_words(command) == ["foo", flag_like_word, "bar"]


class TestResidualShapes:
    """Pins each shape the module docstring names as a residual over-deny or miss."""

    @pytest.mark.parametrize(
        "command",
        [
            'gh pr create --title "$(echo "it\'s")" --body-file /x',
            "git commit -m $'it\\'s' -F /x",
        ],
    )
    def test_valid_bash_that_leaves_quote_parity_open_cannot_be_tokenized(self, command):
        with pytest.raises(ValueError):
            split_command_words(command)

    def test_quoted_opener_mention_followed_by_matching_line_drops_the_lines_between(self):
        command = (
            'echo "see <<WORD in text"\ngh pr create --body-file /y --repo r\nWORD\n'
            "gh pr create --body-file /z"
        )
        words = split_command_words(command)
        assert "/y" not in words
        assert words[-2:] == ["--body-file", "/z"]

    @pytest.mark.parametrize(
        "command",
        [
            "# create it via <<EOF\ngh pr create --body-file /y\nEOF\ngh pr create --body-file /z",
            "echo $((1<<3))\ngh pr create --body-file /y\n3\ngh pr create --body-file /z",
        ],
        ids=["opener-in-comment-line", "arithmetic-shift"],
    )
    def test_opener_in_a_comment_line_or_arithmetic_shift_drops_the_lines_between(self, command):
        words = split_command_words(command)
        assert "/y" not in words
        assert words[-2:] == ["--body-file", "/z"]

    def test_comment_line_drop_is_quote_blind_and_hides_a_flag_after_the_closing_quote(self):
        """Bash keeps the `#` line inside the quoted --title, so its closing quote exposes --body-file."""
        command = 'gh pr create --title "x\n# " --body-file /hidden --title "z\nfinal"'
        words = split_command_words(command)
        assert "/hidden" not in words
        assert words == ["gh", "pr", "create", "--title", "x final"]

    def test_inline_comment_holding_an_unbalanced_quote_exits_3(self):
        result = run_splitter(b"git commit -F /x  # don't amend\n")
        assert result.returncode == 3
        assert result.stdout == b""


class TestInterpreterIsolation:
    def test_planted_stdlib_module_beside_the_script_is_not_imported(self, tmp_path):
        script_copy = tmp_path / "split-command-words.py"
        shutil.copy(SPLITTER_PATH, script_copy)
        (tmp_path / "re.py").write_text('raise RuntimeError("planted re.py imported")\n')
        without_isolation = subprocess.run(
            [sys.executable, "-S", str(script_copy)], input=b"echo x\n", capture_output=True, check=False
        )
        assert without_isolation.returncode != 0, "precondition: without -I the planted re.py is on sys.path"

        result = run_splitter(b"echo x\n", script_path=script_copy)

        assert result.returncode == 0, result.stderr
        assert result.stdout == b"echo\nx\n"


# Modules that give a script a process, network, filesystem, or dynamic-code capability.
# The tokenizer only transforms stdin to stdout, so none of them belongs in it.
CAPABILITY_MODULES = frozenset({
    "subprocess", "socket", "ssl", "os", "pathlib", "shutil", "tempfile", "glob", "ctypes",
    "importlib", "urllib", "http", "ftplib", "smtplib", "multiprocessing", "threading",
    "runpy", "pickle", "marshal", "shelve", "code", "codeop", "pty", "signal", "webbrowser",
})  # fmt: skip


def _top_level_imports(script_path) -> set[str]:
    tree = ast.parse(script_path.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative imports cannot resolve for a standalone script"
            imported.add(node.module.split(".")[0])
    return imported


def test_script_imports_only_standard_library_modules_without_process_network_or_filesystem_capability():
    """The fallback runs under `python3 -I -S`, so a third-party import would fail inside the gate.

    The capability list keeps a later stdlib import from giving a gate-side script a way to run
    commands, reach the network, touch files, or load code. An exact-set pin would also reject a
    harmless new stdlib import, so the pin is the stdlib boundary minus those capabilities.
    """
    imported = _top_level_imports(SPLITTER_PATH)
    assert imported, "vacuous pin: the script's imports were not found"
    assert imported <= set(sys.stdlib_module_names)
    assert not imported & CAPABILITY_MODULES


def test_capability_pin_detects_a_capability_import(tmp_path):
    """Guards the pin above against going vacuous: a capability import must be seen as one."""
    script_with_capability = tmp_path / "with_capability.py"
    script_with_capability.write_text("import re\nfrom subprocess import run\n")
    assert _top_level_imports(script_with_capability) & CAPABILITY_MODULES == {"subprocess"}
