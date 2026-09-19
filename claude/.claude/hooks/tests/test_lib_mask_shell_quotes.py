"""Unit tests for _lib.sh's _lib_mask_shell_quotes, the single-pass quote
masker deny-invisible-commit-content.sh runs ahead of its arm 2 fragment
count.

These source _lib.sh directly and call the function -- no hook invocation,
no JSON payload -- mirroring test_lib_pseudo_file_path.py. The hook's own
test file keeps the end-to-end deny/allow cases proving the masked text
drives the right verdict.
"""
from __future__ import annotations

import subprocess

import pytest
from helpers import HOOKS_DIR, build_path_without

LIB_SH = HOOKS_DIR / "_lib.sh"


def _mask_shell_quotes(
    text: str, env: dict[str, str] | None = None, expect_success: bool = True
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["bash", "-c", f'. "{LIB_SH}"; _lib_mask_shell_quotes "$1"', "_", text],
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

    def test_ansi_c_multi_word_span_has_no_stray_dollar(self):
        """A multi-word ANSI-C-quoted span ($'fix && bar') falls into the
        blanking branch (its interior isn't a single safe word), which must
        trim the leading `$` the same way the single-safe-word unquoting
        branch already does — a `$` before an opening delimiter is dropped
        whenever the span closes — otherwise the blanked output is `$''`
        instead of `''`."""
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
