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
