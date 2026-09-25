"""Unit tests for _lib.sh's _lib_mask_shell_quotes, the single-pass quote
masker deny-invisible-commit-content.sh runs ahead of its count of
git-commit-invoking fragments.

These source _lib.sh directly and call the function -- no hook invocation,
no JSON payload -- mirroring test_lib_pseudo_file_path.py. The hook's own
test file keeps the end-to-end deny/allow cases proving the masked text
drives the right verdict.
"""
from __future__ import annotations

import os
import subprocess

import pytest
from helpers import HOOKS_DIR, build_path_without

LIB_SH = HOOKS_DIR / "_lib.sh"


def _mask_shell_quotes(
    text: str, env: dict[str, str] | None = None, expect_success: bool = True
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["/bin/bash", "-c", f'. "{LIB_SH}"; _lib_mask_shell_quotes "$1"', "_", text],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if expect_success:
        assert result.returncode == 0, result.stderr
    return result


class TestLibMaskShellQuotes:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ('"git" commit', "git commit"),
            ("'git' commit", "git commit"),
            ('"./scripts/a-b_c.sh" x', "./scripts/a-b_c.sh x"),
        ],
    )
    def test_single_safe_word_span_is_emitted_unquoted(self, text, expected):
        """A span whose whole interior matches `^[A-Za-z0-9._/-]+$` loses its
        delimiters so a quoted command word stays visible to a fragment
        count."""
        assert _mask_shell_quotes(text).stdout == expected

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ('g"it" commit', "git commit"),
            ('"gi"t commit', "git commit"),
        ],
    )
    def test_safe_word_span_glued_to_adjacent_text_re_forms_the_word(self, text, expected):
        """A single-safe-word span is unquoted wherever it sits in a word, not
        only at a word boundary, so a mid-word quote split of one safe word
        cannot hide a command word from a fragment count."""
        assert _mask_shell_quotes(text).stdout == expected

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("$'git' commit", "git commit"),
            ('$"git" commit', "git commit"),
        ],
    )
    def test_dollar_prefixed_opener_drops_the_dollar_on_unquote(self, text, expected):
        assert _mask_shell_quotes(text).stdout == expected

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("git commit -m $'fix && bar'", "git commit -m ''"),
            ('git commit -m $"fix && bar"', 'git commit -m ""'),
            ("git commit -m $'msg' x", "git commit -m msg x"),
        ],
    )
    def test_dollar_trim_keeps_text_preceding_the_span(self, text, expected):
        """The `$` trim removes only the one character before the opener, not
        the output accumulated ahead of it."""
        assert _mask_shell_quotes(text).stdout == expected

    @pytest.mark.parametrize("text", ["echo $'abc", 'echo $"abc'])
    def test_dollar_before_an_unterminated_quote_is_kept(self, text):
        assert _mask_shell_quotes(text).stdout == text

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ('-m "two words" && "git" commit -m y', '-m "" && git commit -m y'),
            ('"a" "b"', "a b"),
        ],
    )
    def test_each_span_is_judged_on_its_own_interior(self, text, expected):
        """Interior text from an earlier span must not leak into a later
        span's safe-word check, whether the earlier span was blanked or
        unquoted."""
        assert _mask_shell_quotes(text).stdout == expected

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ('"abc123"', "abc123"),
            ('"ABC"', "ABC"),
            ('"a&b"', '""'),
            ('"a|b"', '""'),
            ('"a$b"', '""'),
            ('"a`b"', '""'),
        ],
    )
    def test_safe_word_class_boundaries(self, text, expected):
        """Digits and uppercase letters are safe-word characters; operator
        characters (`&`, `|`, `$`, backtick) with no whitespace are not."""
        assert _mask_shell_quotes(text).stdout == expected

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ('-m "fix && bar"', '-m ""'),
            ("-m 'fix && bar'", "-m ''"),
            ('-m "two words"', '-m ""'),
            ('-m "a;b"', '-m ""'),
        ],
    )
    def test_multi_word_or_operator_span_blanks_to_its_delimiter_pair(self, text, expected):
        assert _mask_shell_quotes(text).stdout == expected

    def test_backslash_escaped_quote_opens_a_span_known_gap(self):
        """Pinned known gap: the masker does not model backslash escapes, so
        the `\\"` pair below opens and closes a span the shell never sees and
        the second commit is blanked. Changing this output changes which
        commands arm 2 of deny-invisible-commit-content.sh can see."""
        text = 'git commit -m x && echo \\" && git commit -m y && echo \\"'
        assert _mask_shell_quotes(text).stdout == 'git commit -m x && echo \\""'

    def test_ansi_c_multi_word_span_has_no_stray_dollar(self):
        """A `$` before an opening delimiter is dropped whenever the span
        closes, so `$'fix && bar'` masks to `''`, not `$''`."""
        assert _mask_shell_quotes("$'fix && bar'").stdout == "''"

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ('git commit -m "unterminated && x', 'git commit -m "unterminated && x'),
            ("git commit -m 'unterminated && x", "git commit -m 'unterminated && x"),
            ('echo "ok && x" && echo "open && y', 'echo "" && echo "open && y'),
        ],
    )
    def test_unterminated_quote_is_left_unmasked(self, text, expected):
        """A quote left open at end of string keeps its tail verbatim so a
        real second commit fragment there still reaches the caller's scan."""
        assert _mask_shell_quotes(text).stdout == expected

    def test_opposite_quote_type_inside_span_is_masked_as_content(self):
        assert _mask_shell_quotes("""-m "it's && done" x""").stdout == '-m "" x'

    def test_text_without_quotes_passes_through_unchanged(self):
        assert _mask_shell_quotes("git add f && git commit").stdout == "git add f && git commit"

    def test_empty_input_yields_empty_output(self):
        assert _mask_shell_quotes("").stdout == ""

    def test_empty_quoted_span_blanks_to_its_delimiter_pair(self):
        assert _mask_shell_quotes('x "" y').stdout == 'x "" y'

    def test_span_with_embedded_newline_is_scanned_as_one_span(self):
        assert _mask_shell_quotes('-m "line1\ngit commit"').stdout == '-m ""'

    def test_multibyte_span_blanks_to_its_delimiter_pair(self):
        assert _mask_shell_quotes('-m "café && x"').stdout == '-m ""'

    def test_awk_absent_exits_non_zero_so_callers_can_fail_closed(self, tmp_path):
        farm_dir = tmp_path / "path-without-awk"
        farm_dir.mkdir()
        result = _mask_shell_quotes(
            '"git" commit', env={"PATH": build_path_without("awk", farm_dir)}, expect_success=False
        )
        assert result.returncode != 0

    def test_timeout_exit_status_propagates_so_callers_can_fail_closed(self, tmp_path):
        """A `timeout` that kills the scan exits 124, and that status must
        reach the caller. A shim stands in for the real 5s wait."""
        shim_dir = tmp_path / "timeout-shim-bin"
        shim_dir.mkdir()
        shim = shim_dir / "timeout"
        shim.write_text("#!/bin/bash\nexit 124\n")
        shim.chmod(0o755)
        result = _mask_shell_quotes(
            '"git" commit',
            env={"PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"},
            expect_success=False,
        )
        assert result.returncode == 124
