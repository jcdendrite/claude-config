"""Direct, no-subprocess-under-test unit tests for _review-pr-lib.sh.

review-pr-acquire.sh, review-pr-checkout.sh and review-pr-diff.sh reach these
checks only through a full script invocation against a PATH-shimmed gh. These
tests source hooks/_lib.sh and _review-pr-lib.sh standalone (never through a
script) against the real jq, and pin each helper's contract over its input
matrix, so each script's own tests need only one deny and one allow wiring
case per check. One row, in TestAuditVerdict, also runs the real
audit-execution-surface.py to pin the clean document it prints.

A file list reaches the helpers as JSON text, so a control character in a test
name travels as a JSON escape (json.dumps' default) rather than a raw byte.
"""
from __future__ import annotations

import json
import subprocess

import pytest
from helpers import HOOKS_DIR, SCRIPTS_DIR, SKILLS_DIR

from .conftest import _base_test_env

_LIB = SCRIPTS_DIR / "_review-pr-lib.sh"
_HOOKS_LIB = HOOKS_DIR / "_lib.sh"

_EXPECTED_JQ_FILTER = ".[].filename | @json"

# A harness bound so a hung bash or interpreter fails one test instead of the suite.
_SUBPROCESS_TIMEOUT_SECONDS = 60


def _run_bash(script_body: str, *args: str) -> subprocess.CompletedProcess:
    """Source hooks/_lib.sh then _review-pr-lib.sh, then run script_body under
    `set -euo pipefail`, the way the scripts source them. args arrive as "$1",
    "$2", ... so a test input never needs shell quoting."""
    full_script = f'set -euo pipefail\n. "{_HOOKS_LIB}"\n. "{_LIB}"\n{script_body}\n'
    return subprocess.run(
        ["bash", "-c", full_script, "bash", *args],
        capture_output=True,
        text=True,
        check=False,
        env=_base_test_env(),
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    )


def _call(function_name: str, *args: str, defined_statuses: frozenset[str] = frozenset({"0", "1"})) -> tuple[int, str]:
    """Call function_name with args, returning (exit status, stdout).

    The status is captured with `|| status=$?` and must be one of
    defined_statuses (0 or 1 unless the function documents more), so a deny
    row cannot pass vacuously on a bash error (127 command-not-found).
    """
    result = _run_bash(
        f'status=0\noutput=$({function_name} "$@") || status=$?\nprintf "%s\\n%s" "$status" "$output"',
        *args,
    )
    assert result.returncode == 0, result.stderr
    status_line, _, output = result.stdout.partition("\n")
    assert status_line in defined_statuses, f"{function_name} exited with undefined status {status_line!r}: {result.stderr}"
    return int(status_line), output


class TestCallHelper:
    def test_nonexistent_function_fails_the_helper_assertion(self):
        with pytest.raises(AssertionError, match="undefined status '127'"):
            _call("review_pr_no_such_function", "anything")


class TestFileNamesJqFilter:
    def test_constant_is_the_one_json_string_per_line_filter(self):
        result = _run_bash('printf "%s" "$REVIEW_PR_FILE_NAMES_JQ_FILTER"')
        assert result.stdout == _EXPECTED_JQ_FILTER

    def test_filter_keeps_a_newline_name_on_one_line(self):
        page = json.dumps([{"filename": "a.py"}, {"filename": "docs/notes.txt\nevil.sh"}])
        result = _run_bash('printf "%s" "$1" | jq -r "$REVIEW_PR_FILE_NAMES_JQ_FILTER"', page)
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == ['"a.py"', '"docs/notes.txt\\nevil.sh"']


class TestRestChangedFiles:
    @pytest.mark.parametrize(
        "payload,expected",
        [
            ('{"changed_files": 12}', "12"),
            ('{"changed_files": 0}', "0"),
            ('{"changed_files": 3001, "commits": 4}', "3001"),
            ('{"changed_files": "12"}', "12"),
        ],
        ids=["typical", "zero", "beyond-listing-cap", "integer-text"],
    )
    def test_integer_count_or_integer_text_is_printed(self, payload, expected):
        assert _call("review_pr_rest_changed_files", payload) == (0, expected)

    @pytest.mark.parametrize(
        "payload",
        [
            "{}",
            '{"changed_files": null}',
            '{"changed_files": false}',
            '{"changed_files": true}',
            '{"changed_files": "abc"}',
            '{"changed_files": ""}',
            '{"changed_files": 1.5}',
            '{"changed_files": -1}',
            '{"changed_files": [3]}',
            "not json",
            "",
            "[]",
        ],
        ids=[
            "missing", "null", "false", "true", "string", "empty-string",
            "fractional", "negative", "array", "non-json", "empty-input", "non-object",
        ],
    )
    def test_a_value_that_does_not_print_as_a_non_negative_integer_is_refused_with_no_output(self, payload):
        assert _call("review_pr_rest_changed_files", payload) == (1, "")


class TestDecodeFileNames:
    def test_stream_of_json_strings_decodes_into_one_array(self):
        assert _call("review_pr_decode_file_names", '"a.py"\n"b.py"\n') == (0, '["a.py","b.py"]')

    def test_a_newline_inside_a_name_stays_one_element(self):
        status, output = _call("review_pr_decode_file_names", '"docs/notes.txt\\nevil.sh"\n"a.py"\n')
        assert status == 0
        assert json.loads(output) == ["docs/notes.txt\nevil.sh", "a.py"]

    def test_non_ascii_quote_and_space_names_decode_intact(self):
        names = ["docs/café notes.md", 'say "hi".txt', "\U0001f600.py"]
        raw = "".join(json.dumps(name, ensure_ascii=False) + "\n" for name in names)
        status, output = _call("review_pr_decode_file_names", raw)
        assert status == 0
        assert json.loads(output) == names

    def test_empty_stream_decodes_to_an_empty_array(self):
        assert _call("review_pr_decode_file_names", "") == (0, "[]")

    def test_a_stream_that_is_not_json_is_refused(self):
        assert _call("review_pr_decode_file_names", '"a.py"\nnot json\n')[0] == 1


class TestFileCountMatches:
    @pytest.mark.parametrize(
        "files_json,expected_count",
        [('["a.py","b.py"]', "2"), ("[]", "0")],
        ids=["two", "empty-listing"],
    )
    def test_equal_length_prints_the_count_and_succeeds(self, files_json, expected_count):
        assert _call("review_pr_file_count_matches", files_json, expected_count) == (0, expected_count)

    @pytest.mark.parametrize(
        "files_json,expected_count,listed_count",
        [
            ('["a.py"]', "2", "1"),
            ('["a.py","b.py"]', "1", "2"),
            ('["a.py","b.py","c.py"]', "3001", "3"),
            ('["a.py"]', "", "1"),
        ],
        ids=["shorter", "longer", "truncated-far-short", "empty-expected"],
    )
    def test_differing_length_prints_the_count_and_fails(self, files_json, expected_count, listed_count):
        assert _call("review_pr_file_count_matches", files_json, expected_count) == (1, listed_count)

    @pytest.mark.parametrize(
        "files_json",
        ['{"a": 1}', '"abc"', "null", "3", "not json", ""],
        ids=["object", "string", "null", "number", "non-json", "empty-input"],
    )
    def test_input_that_is_not_an_array_fails_with_no_output(self, files_json):
        assert _call("review_pr_file_count_matches", files_json, "0") == (1, "")


def _names_json(*names: str) -> str:
    return json.dumps(list(names))


# 0 = no refused character, 1 = one found, 2 = could not be decided.
_LINE_SAFE_STATUSES = frozenset({"0", "1", "2"})


class TestFileNamesLineSafe:
    @pytest.mark.parametrize(
        "files_json",
        [
            _names_json(),
            _names_json("a.py"),
            _names_json("docs/notes.md", "src/app.py"),
            _names_json("docs/café notes.md"),
            _names_json('say "hi".txt'),
            _names_json("with space.py"),
            _names_json("a\u00a0b.py"),
            _names_json("a\u2028b.py"),
            _names_json("\U0001f600.py"),
            _names_json("a~b", "a_b", "a[b]", "a]b"),
            "[1, null, true]",
        ],
        ids=[
            "empty-listing", "plain", "several", "non-ascii", "quote", "space",
            "first-printable-latin1-160", "line-separator", "non-bmp",
            "neighbours-of-caret", "non-string-elements",
        ],
    )
    def test_names_without_a_refused_character_are_safe(self, files_json):
        assert _call("review_pr_file_names_line_safe", files_json, defined_statuses=_LINE_SAFE_STATUSES) == (0, "")

    @pytest.mark.parametrize(
        "name",
        [
            "a\nb.py",
            "a\tb.py",
            "a\rb.py",
            "a\x0bb.py",
            "a\x00b.py",
            "a\x1bb.py",
            "a\x1fb.py",
            "a\x7fb.py",
            "a\u0080b.py",
            "a\u0085b.py",
            "a\u009fb.py",
            "a^[b.py",
            "a^@b.py",
            "^leading.py",
            "trailing^",
        ],
        ids=[
            "lf", "tab", "cr", "vt", "nul", "esc", "unit-separator-31", "del",
            "c1-128", "c1-nel", "c1-159", "caret-esc", "caret-nul", "caret-leading", "caret-trailing",
        ],
    )
    def test_a_refused_character_in_any_name_is_unsafe(self, name):
        assert _call("review_pr_file_names_line_safe", _names_json("a.py", name), defined_statuses=_LINE_SAFE_STATUSES) == (1, "")

    @pytest.mark.parametrize(
        "files_json",
        ["not json", "", "null", '"abc"', '{"name": "a.py"}', "3"],
        ids=["non-json", "empty-input", "null", "string", "object", "number"],
    )
    def test_input_that_cannot_be_checked_is_undecided_rather_than_unsafe(self, files_json):
        assert _call("review_pr_file_names_line_safe", files_json, defined_statuses=_LINE_SAFE_STATUSES) == (2, "")


class TestAuditVerdict:
    """review_pr_audit_verdict: `clean` needs status 0 with the audit's exact
    clean document and `stop` needs status 1 with a `stop == true` object, so
    a tooling failure is never mistaken for a verdict."""

    @pytest.mark.parametrize(
        "status,stdout,verdict",
        [
            ("0", '{"stop": false, "matches": []}', "clean"),
            ("0", "", "failed"),
            ("0", '{"stop": false}', "failed"),
            ("0", '{"stop": false, "matches": []}\n{"stop": true, "matches": []}', "failed"),
            ("1", '{"stop": true, "matches": [{"path": ".mcp.json", "reason": "r"}]}', "stop"),
            ("1", "", "failed"),
            ("127", "", "failed"),
            ("2", '{"error": "invalid JSON on stdin"}', "failed"),
        ],
        ids=[
            "clean", "status-0-empty-stdout", "status-0-stop-false-without-matches",
            "status-0-clean-document-followed-by-another-line", "stop",
            "status-1-empty-stdout", "python3-missing", "malformed-input",
        ],
    )
    def test_only_a_documented_status_with_its_documented_stdout_is_a_verdict(self, status, stdout, verdict):
        assert _call("review_pr_audit_verdict", status, stdout) == (0, verdict)

    def test_the_clean_document_is_what_the_real_audit_prints_for_an_empty_file_list(self):
        audit_script = SKILLS_DIR / "review-pr" / "audit-execution-surface.py"
        audit_run = subprocess.run(
            ["python3", "-I", str(audit_script)],
            input="[]",
            capture_output=True,
            text=True,
            check=False,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
        clean_document = _run_bash('printf "%s" "$REVIEW_PR_AUDIT_CLEAN_DOCUMENT"')

        assert audit_run.returncode == 0, audit_run.stderr
        assert audit_run.stdout == clean_document.stdout + "\n"


class TestAuditStdoutExcerpt:
    def test_a_first_line_longer_than_the_limit_is_cut_and_marked_truncated(self):
        echo_limit = int(_run_bash('printf "%s" "$REVIEW_PR_AUDIT_STDOUT_ECHO_LIMIT"').stdout)
        long_first_line = "a" * (echo_limit + 50)

        excerpt = _call("review_pr_audit_stdout_excerpt", f"{long_first_line}\nsecond line")

        assert excerpt == (0, "a" * echo_limit + "...[truncated]")


class TestGhStatusDescription:
    def test_a_cap_kill_status_reads_as_a_timeout(self):
        assert _call("review_pr_gh_status_description", "124") == (0, "timed out")

    @pytest.mark.parametrize("status", ["1", "4", "127", "137", "143"])
    def test_every_other_status_reads_as_a_failure_naming_the_status(self, status):
        assert _call("review_pr_gh_status_description", status) == (0, f"failed (exit {status})")

