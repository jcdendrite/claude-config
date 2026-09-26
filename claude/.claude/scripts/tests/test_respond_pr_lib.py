"""Direct, no-subprocess-under-test unit tests for _respond-pr-lib.sh.

respond-pr-safe-patch.sh reaches these checks only through a full script
invocation against a PATH-shimmed gh. These tests source _respond-pr-lib.sh
standalone (never through the script) and pin each predicate's contract over
its input matrix, so the script's own tests need only one wiring case per
check.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from .conftest import _base_test_env

_LIB = Path(__file__).parent.parent / "_respond-pr-lib.sh"

_MARKER = "**[Claude Code]**"


def _en_us_utf8_widens_digit_bracket_matching() -> bool:
    """True only when this runner's en_US.UTF-8 makes bash's `[0-9]` bracket
    expression match a non-ASCII decimal digit; if it collapses to C, the
    LC_ALL=C fix below is untestable here. Targets `[0-9]` specifically
    (not `[a-z]`, as claude/.claude/hooks/tests/test_lib_path_char_allowlist.py's
    parallel probe does) because the two constructs can diverge on the same
    bash build: this repo's own pinned bash reproduces the digit widening
    but not the letter one."""
    probe = subprocess.run(
        ["bash", "-c", '[[ "٤" =~ ^[0-9]$ ]]'],
        capture_output=True,
        env={**_base_test_env(), "LC_ALL": "en_US.UTF-8"},
        check=False,
    )
    assert probe.returncode in (0, 1), (probe.returncode, probe.stderr)
    return probe.returncode == 0


@pytest.fixture(scope="module")
def utf8_locale_is_functional() -> bool:
    return _en_us_utf8_widens_digit_bracket_matching()


def _skip_if_utf8_locale_not_functional(utf8_locale_is_functional: bool) -> None:
    if not utf8_locale_is_functional:
        pytest.skip(
            "en_US.UTF-8 did not widen [0-9] to admit a non-ASCII digit on this "
            "bash/glibc, so the LC_ALL=C pin is invisible here."
        )


def _run_bash(script_body: str, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Source _respond-pr-lib.sh, then run script_body under `set -euo pipefail`.

    args arrive as "$1", "$2", ... inside script_body, so a test input never
    needs shell quoting. env defaults to the credential-scrubbed base test
    env, so no gh credential reaches the sourced lib.
    """
    full_script = f'set -euo pipefail\n. "{_LIB}"\n{script_body}\n'
    return subprocess.run(
        ["bash", "-c", full_script, "bash", *args],
        capture_output=True,
        text=True,
        check=False,
        env=_base_test_env() if env is None else env,
    )


def _predicate_result(function_name: str, value: str, *, env: dict[str, str] | None = None) -> bool:
    """Call function_name with value as $1 and return its truth value.

    The call's exit status is captured with `|| predicate_status=$?` and
    echoed. Status 0 returns True and status 1 returns False. Any other
    status (127 command-not-found, 2 regex error) fails the assertion here,
    so a deny row cannot pass vacuously on a bash error.
    """
    result = _run_bash(
        f'predicate_status=0\n{function_name} "$1" || predicate_status=$?\necho "$predicate_status"',
        value,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() in {"0", "1"}, (
        f"{function_name} exited with undefined status {result.stdout.strip()!r}: {result.stderr}"
    )
    return result.stdout.strip() == "0"


class TestPredicateResultHelper:
    def test_nonexistent_function_fails_the_helper_assertion(self):
        expected_message = re.escape("respond_pr_no_such_function exited with undefined status '127'")
        with pytest.raises(AssertionError, match=expected_message):
            _predicate_result("respond_pr_no_such_function", "anything")


class TestOwnershipMarkerConstant:
    def test_constant_is_the_literal_marker(self):
        result = _run_bash('printf "%s" "$RESPOND_PR_OWNERSHIP_MARKER"')
        assert result.returncode == 0
        assert result.stdout == _MARKER


class TestValidRepoSlug:
    @pytest.mark.parametrize("slug", [
        "owner/repo",
        "my-org/my_repo.name",
        "a/b",
        "Owner123/Repo.js",
        "owner/.github",
    ])
    def test_well_formed_slug_is_valid(self, slug):
        assert _predicate_result("respond_pr_valid_repo_slug", slug) is True

    @pytest.mark.parametrize("slug", ["owner/..", "../repo", "../..", "owner/."])
    def test_all_dot_segment_is_accepted_by_the_shape_check(self, slug):
        """Pins that the predicate accepts an all-dot segment: `.` is in its
        allowed character class. This asserts only what the shape check
        accepts, not that GitHub's own routing treats these slugs as safe."""
        assert _predicate_result("respond_pr_valid_repo_slug", slug) is True

    @pytest.mark.parametrize("slug", [
        "owner/repo/../../other-org/other-repo",
        "owner",
        "owner/repo/extra",
        "/owner/repo",
        "owner/repo/",
        "/",
        "owner/",
        "/repo",
        "owner /repo",
        "owner/repo\n",
        "日本/repo",
        "owner/rëpo☃",
        "",
    ])
    def test_malformed_slug_is_invalid(self, slug):
        assert _predicate_result("respond_pr_valid_repo_slug", slug) is False

    def test_non_ascii_slug_is_invalid_under_utf8_caller_locale(self, utf8_locale_is_functional):
        """Pins the predicate's own LC_ALL=C override: under a UTF-8 caller
        locale, glibc's bracket-expression collation otherwise widens
        [A-Za-z0-9._-] to accept non-ASCII lookalikes. "日本/repo" is not used
        here: CJK code points have no equivalence-class entry against
        [A-Za-z0-9._-], so that case is already covered by
        test_malformed_slug_is_invalid without discriminating this fix."""
        _skip_if_utf8_locale_not_functional(utf8_locale_is_functional)
        utf8_env = {**_base_test_env(), "LC_ALL": "en_US.UTF-8", "LANG": "en_US.UTF-8"}
        assert _predicate_result("respond_pr_valid_repo_slug", "owner/rëpo", env=utf8_env) is False

    def test_well_formed_slug_is_valid_under_utf8_caller_locale(self, utf8_locale_is_functional):
        """Pairs with test_non_ascii_slug_is_invalid_under_utf8_caller_locale:
        the same forced locale must not also reject legitimate ASCII input."""
        _skip_if_utf8_locale_not_functional(utf8_locale_is_functional)
        utf8_env = {**_base_test_env(), "LC_ALL": "en_US.UTF-8", "LANG": "en_US.UTF-8"}
        assert _predicate_result("respond_pr_valid_repo_slug", "owner/repo", env=utf8_env) is True


class TestValidCommentId:
    @pytest.mark.parametrize("comment_id", ["0", "42", "007", "9" * 40])
    def test_numeric_id_is_valid(self, comment_id):
        assert _predicate_result("respond_pr_valid_comment_id", comment_id) is True

    @pytest.mark.parametrize("comment_id", [
        "42/../../999",
        "abc",
        "-1",
        "+1",
        "1.5",
        "4 2",
        "42\n",
        "٤٢",
        "",
    ])
    def test_non_numeric_id_is_invalid(self, comment_id):
        assert _predicate_result("respond_pr_valid_comment_id", comment_id) is False

    def test_arabic_indic_digits_are_invalid_under_utf8_caller_locale(self, utf8_locale_is_functional):
        """Pins the predicate's own LC_ALL=C override: under a UTF-8 caller
        locale, glibc's bracket-expression collation otherwise widens [0-9]
        to accept non-ASCII decimal digits such as Arabic-Indic ٤٢."""
        _skip_if_utf8_locale_not_functional(utf8_locale_is_functional)
        utf8_env = {**_base_test_env(), "LC_ALL": "en_US.UTF-8", "LANG": "en_US.UTF-8"}
        assert _predicate_result("respond_pr_valid_comment_id", "٤٢", env=utf8_env) is False

    def test_numeric_id_is_valid_under_utf8_caller_locale(self, utf8_locale_is_functional):
        """Pairs with test_arabic_indic_digits_are_invalid_under_utf8_caller_locale:
        the same forced locale must not also reject a legitimate numeric id."""
        _skip_if_utf8_locale_not_functional(utf8_locale_is_functional)
        utf8_env = {**_base_test_env(), "LC_ALL": "en_US.UTF-8", "LANG": "en_US.UTF-8"}
        assert _predicate_result("respond_pr_valid_comment_id", "42", env=utf8_env) is True


class TestLocalePinDoesNotLeak:
    @pytest.mark.parametrize("function_name, valid_input", [
        ("respond_pr_valid_repo_slug", "owner/repo"),
        ("respond_pr_valid_comment_id", "42"),
    ])
    def test_caller_locale_is_unaffected_by_the_predicates_own_pin(self, function_name, valid_input):
        """Each predicate's LC_ALL=C runs in a subshell body (see
        _lib_passes_path_char_allowlist in claude/.claude/hooks/_lib.sh for
        the same pattern), so it must not leak into the caller's own shell."""
        result = _run_bash(
            f'LC_ALL=en_US.UTF-8\n{function_name} "$1"\nprintf %s "$LC_ALL"',
            valid_input,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "en_US.UTF-8"


class TestBodyIsBlank:
    @pytest.mark.parametrize("body", ["", " ", "\t", "\n", " \t\n  ", "\r\n"])
    def test_empty_or_whitespace_only_body_is_blank(self, body):
        assert _predicate_result("respond_pr_body_is_blank", body) is True

    @pytest.mark.parametrize("body", ["x", " x ", "\n.\n", _MARKER])
    def test_body_with_content_is_not_blank(self, body):
        assert _predicate_result("respond_pr_body_is_blank", body) is False

    @pytest.mark.parametrize("whitespace_only_body", [" ", "\t", "\n"])
    def test_ascii_whitespace_only_body_is_blank_under_c_locale(self, whitespace_only_body):
        """Pins that ASCII whitespace-only bodies are blank under LC_ALL=C.
        Bodies made only of non-ASCII whitespace (NBSP, U+3000, U+2003) are
        locale-dependent under [[:space:]] and intentionally unpinned."""
        c_locale_env = {**_base_test_env(), "LC_ALL": "C", "LANG": "C"}
        assert _predicate_result("respond_pr_body_is_blank", whitespace_only_body, env=c_locale_env) is True


class TestBodyIsClaudeMarked:
    @pytest.mark.parametrize("body", [
        _MARKER,
        f"{_MARKER} original body",
        f"{_MARKER}\n\nsecond paragraph",
        f"{_MARKER}{_MARKER}",
    ])
    def test_body_starting_with_marker_is_marked(self, body):
        assert _predicate_result("respond_pr_body_is_claude_marked", body) is True

    @pytest.mark.parametrize("body", [
        "",
        "Please fix this typo.",
        f" {_MARKER} corrected text",
        f"\n{_MARKER}",
        f"text before {_MARKER}",
        "**[claude code]** corrected text",
        "**[CLAUDE CODE]**",
        "**[Claude Code]",
        "[Claude Code]**",
        "**Claude Code**",
    ])
    def test_body_without_marker_prefix_is_not_marked(self, body):
        assert _predicate_result("respond_pr_body_is_claude_marked", body) is False

    @pytest.mark.parametrize("glob_shaped_body", [
        "C",
        "l",
        "**C** text",
    ])
    def test_marker_is_matched_literally_not_as_a_glob(self, glob_shaped_body):
        """An unquoted marker would make [Claude Code] a glob bracket
        expression matching any one character from that set -- each body
        contains such a character, so it matches the glob reading but not
        the literal marker."""
        assert _predicate_result("respond_pr_body_is_claude_marked", glob_shaped_body) is False

    def test_empty_marker_fails_closed_instead_of_marking_every_body(self):
        """An empty marker is a prefix of every body, so the predicate must
        abort rather than report a human comment as Claude-authored."""
        result = _run_bash(
            "RESPOND_PR_OWNERSHIP_MARKER=''\nrespond_pr_body_is_claude_marked \"$1\" && echo marked",
            "Please fix this typo.",
        )
        assert result.returncode != 0
        assert "marked" not in result.stdout
        assert "RESPOND_PR_OWNERSHIP_MARKER" in result.stderr
