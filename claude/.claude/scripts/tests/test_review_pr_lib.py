"""Direct, no-subprocess-under-test unit tests for _review-pr-lib.sh.

review-pr-acquire.sh, review-pr-checkout.sh and review-pr-diff.sh reach these
checks only through a full script invocation against a PATH-shimmed gh. These
tests source hooks/_lib.sh and _review-pr-lib.sh standalone (never through a
script) against the real jq, and pin each helper's contract over its input
matrix, so each script's own tests need only one deny and one allow wiring
case per check. One row, in TestAuditVerdict, also runs the real
audit-execution-surface.py to pin the clean document it prints.
"""
from __future__ import annotations

import json
import subprocess

import pytest
from helpers import HOOKS_DIR, SCRIPTS_DIR, SKILLS_DIR

from .conftest import _base_test_env, _review_pr_audit_limit

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


def _call(function_name: str, *args: str) -> tuple[int, str]:
    """Call function_name with args, returning (exit status, stdout).

    The status is captured with `|| status=$?` and must be 0 or 1, so a deny
    row cannot pass vacuously on a bash error (127 command-not-found).
    """
    result = _run_bash(
        f'status=0\noutput=$({function_name} "$@") || status=$?\nprintf "%s\\n%s" "$status" "$output"',
        *args,
    )
    assert result.returncode == 0, result.stderr
    status_line, _, output = result.stdout.partition("\n")
    assert status_line in {"0", "1"}, f"{function_name} exited with undefined status {status_line!r}: {result.stderr}"
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


def _run_filter(constant_name: str, page: list[dict]) -> list:
    """Evaluate the named `--jq` filter constant over one REST page with the
    real jq, returning one decoded value per output line. Production runs the
    filters under gh's embedded jq; the jq binary stands in for it here."""
    result = _run_bash(f'printf "%s" "$1" | jq -c "${constant_name}"', json.dumps(page))
    assert result.returncode == 0, result.stderr
    return [json.loads(line) for line in result.stdout.splitlines()]


class TestReviewsJqFilter:
    def test_review_with_an_empty_body_is_excluded(self):
        page = [
            {"id": 1, "user": {"login": "alice"}, "state": "COMMENTED", "body": ""},
            {"id": 2, "user": {"login": "bob"}, "state": "APPROVED", "body": "ship it"},
        ]
        assert [review["id"] for review in _run_filter("REVIEW_PR_REVIEWS_JQ_FILTER", page)] == [2]

    def test_projection_keys_are_exactly_id_author_state_body(self):
        page = [{"id": 2, "user": {"login": "bob"}, "state": "APPROVED", "body": "ship it", "html_url": "x", "commit_id": "y"}]
        (review,) = _run_filter("REVIEW_PR_REVIEWS_JQ_FILTER", page)
        assert review == {"id": 2, "author": "bob", "state": "APPROVED", "body": "ship it"}

    def test_null_user_gives_a_null_author(self):
        page = [{"id": 3, "user": None, "state": "COMMENTED", "body": "from a deleted account"}]
        (review,) = _run_filter("REVIEW_PR_REVIEWS_JQ_FILTER", page)
        assert review["author"] is None


class TestInlineCommentsJqFilter:
    def test_projection_keys_are_exactly_author_path_line_body(self):
        page = [{"id": 9, "user": {"login": "alice"}, "path": "a.py", "line": 12, "body": "nit", "diff_hunk": "@@"}]
        (comment,) = _run_filter("REVIEW_PR_INLINE_COMMENTS_JQ_FILTER", page)
        assert comment == {"author": "alice", "path": "a.py", "line": 12, "body": "nit"}

    def test_null_user_gives_a_null_author(self):
        page = [{"user": None, "path": "a.py", "line": 12, "body": "from a deleted account"}]
        (comment,) = _run_filter("REVIEW_PR_INLINE_COMMENTS_JQ_FILTER", page)
        assert comment["author"] is None

    def test_null_line_survives(self):
        page = [{"user": {"login": "alice"}, "path": "a.py", "line": None, "body": "outdated"}]
        (comment,) = _run_filter("REVIEW_PR_INLINE_COMMENTS_JQ_FILTER", page)
        assert "line" in comment and comment["line"] is None


class TestCommitShasJqFilter:
    def test_filter_prints_one_raw_sha_per_line(self):
        page = [{"sha": "a" * 40, "commit": {"message": "one"}}, {"sha": "b" * 40, "commit": {"message": "two"}}]
        result = _run_bash('printf "%s" "$1" | jq -r "$REVIEW_PR_COMMIT_SHAS_JQ_FILTER"', json.dumps(page))
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == ["a" * 40, "b" * 40]


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


class TestAuditMatchLines:
    @staticmethod
    def _stop_document(*path_reason_pairs: tuple[str, str]) -> str:
        return json.dumps(
            {"stop": True, "matches": [{"path": path, "reason": reason} for path, reason in path_reason_pairs]}
        )

    _limit = staticmethod(_review_pr_audit_limit)

    def test_each_match_is_one_line_with_a_json_string_path_and_a_verbatim_reason(self):
        document = self._stop_document(("a/CLAUDE.md", "loaded as instructions"), (".mcp.json", 'has "quotes"'))

        assert _call("review_pr_audit_match_lines", document) == (
            0,
            '"a/CLAUDE.md": loaded as instructions\n".mcp.json": has "quotes"',
        )

    def test_a_newline_and_an_escape_byte_in_a_path_stay_on_the_one_line(self):
        document = self._stop_document(("x\nsecond line: spoof/CLAUDE.md\x1b[31m", "reason"))

        status, output = _call("review_pr_audit_match_lines", document)

        assert status == 0
        assert output.splitlines() == ['"x\\nsecond line: spoof/CLAUDE.md\\u001b[31m": reason']

    @pytest.mark.parametrize(
        ("character", "expected_escape"),
        [
            ("\x7f", "\\u007f"),
            ("\u0080", "\\u0080"),
            ("\u009b", "\\u009b"),
            ("\u009f", "\\u009f"),
            ("\u200b", "\\u200b"),
            ("\u202e", "\\u202e"),
            ("\u2028", "\\u2028"),
            ("\u2029", "\\u2029"),
            ("\U000e0041", "\\udb40\\udc41"),
            ("\U0001f600", "\\ud83d\\ude00"),
            ("\u00e9", "\\u00e9"),
        ],
        ids=[
            "del",
            "c1_first",
            "c1_csi",
            "c1_last",
            "zero_width_space",
            "bidi_right_to_left_override",
            "line_separator",
            "paragraph_separator",
            "tag_block_letter",
            "astral_emoji",
            "latin_accent",
        ],
    )
    def test_every_character_outside_printable_ascii_is_escaped_as_unicode_escapes(
        self, character, expected_escape
    ):
        document = self._stop_document((f"a{character}b/CLAUDE.md", "reason"))

        status, output = _call("review_pr_audit_match_lines", document)

        assert status == 0
        assert output == f'"a{expected_escape}b/CLAUDE.md": reason'
        assert output.isascii()

    def test_a_path_of_mixed_characters_prints_as_ascii_that_decodes_to_the_original_path(self):
        path = 'dir "quoted"\\back\n\u202e\U000e0041\u0080/CLAUDE.md'
        document = self._stop_document((path, "reason"))

        status, output = _call("review_pr_audit_match_lines", document)

        assert status == 0
        assert output.isascii()
        assert output.isprintable()
        assert json.loads(output.removesuffix(": reason")) == path

    def test_a_path_past_the_length_limit_is_cut_and_marked_truncated(self):
        path_limit = self._limit("REVIEW_PR_AUDIT_PATH_ECHO_LIMIT")
        document = self._stop_document(("p" * (path_limit + 10) + "/CLAUDE.md", "reason"))

        assert _call("review_pr_audit_match_lines", document) == (0, f'"{"p" * path_limit}...[truncated]": reason')

    def test_a_path_exactly_at_the_length_limit_is_not_marked_truncated(self):
        path_limit = self._limit("REVIEW_PR_AUDIT_PATH_ECHO_LIMIT")
        path_at_limit = "p" * path_limit
        document = self._stop_document((path_at_limit, "reason"))

        assert _call("review_pr_audit_match_lines", document) == (0, f'"{path_at_limit}": reason')

    def test_matches_past_the_count_limit_are_replaced_by_a_counting_line_and_their_reason(self):
        match_limit = self._limit("REVIEW_PR_AUDIT_MATCH_LINE_LIMIT")
        document = self._stop_document(*[(f"d{index}/CLAUDE.md", "reason") for index in range(match_limit + 3)])

        status, output = _call("review_pr_audit_match_lines", document)

        assert status == 0
        lines = output.splitlines()
        assert len(lines) == match_limit + 2
        assert lines[-2:] == ["... and 3 more matched path(s) not shown", "not shown: reason (3 path(s))"]

    def test_truncated_listing_names_each_distinct_reason_among_the_unlisted_matches(self):
        match_limit = self._limit("REVIEW_PR_AUDIT_MATCH_LINE_LIMIT")
        listed_matches = [(f"d{index}/CLAUDE.md", "instructions reason") for index in range(match_limit)]
        document = self._stop_document(
            *listed_matches,
            (".claude/settings.json", "settings reason"),
            ("later/.mcp.json", "mcp reason"),
            ("other/.mcp.json", "mcp reason"),
        )

        status, output = _call("review_pr_audit_match_lines", document)

        assert status == 0
        lines = output.splitlines()
        assert lines[match_limit:] == [
            "... and 3 more matched path(s) not shown",
            "not shown: mcp reason (2 path(s))",
            "not shown: settings reason (1 path(s))",
        ]

    def test_a_listing_within_the_count_limit_prints_no_not_shown_lines(self):
        match_limit = self._limit("REVIEW_PR_AUDIT_MATCH_LINE_LIMIT")
        document = self._stop_document(*[(f"d{index}/CLAUDE.md", f"reason {index}") for index in range(match_limit)])

        status, output = _call("review_pr_audit_match_lines", document)

        assert status == 0
        assert "not shown" not in output

    @pytest.mark.parametrize("not_a_stop_document", ["", "not json", '{"stop": true}', '{"matches": "x"}'])
    def test_a_document_without_a_matches_array_returns_one(self, not_a_stop_document):
        status, output = _call("review_pr_audit_match_lines", not_a_stop_document)

        assert (status, output) == (1, "")

    def test_a_document_with_a_formattable_entry_before_an_unformattable_one_prints_nothing(self):
        document = '{"stop": true, "matches": [{"path": "ok/CLAUDE.md", "reason": "reason"}, 3]}'

        assert _call("review_pr_audit_match_lines", document) == (1, "")

    # Every rule in audit-execution-surface.py, one path each, so the worst case below
    # carries each reason the audit can print.
    _ONE_PATH_PER_AUDIT_REASON = (
        ".gitattributes",
        ".githooks/pre-commit",
        "CLAUDE.md",
        "CLAUDE.local.md",
        "AGENTS.md",
        ".claude/skills/a/SKILL.md",
        ".claude/settings.json",
        ".claude/settings.local.json",
        ".claude/hooks/a.sh",
        ".claude/agents/a.md",
        ".claude/rules/a.md",
        ".claude/output-styles/a.md",
        ".claude/commands/a.md",
        ".mcp.json",
        ".gitmodules",
    )

    def test_the_worst_case_document_prints_fewer_bytes_than_the_harness_truncation_threshold(self):
        audit_script = SKILLS_DIR / "review-pr" / "audit-execution-surface.py"
        audit_run = subprocess.run(
            ["python3", "-I", str(audit_script)],
            input=json.dumps(list(self._ONE_PATH_PER_AUDIT_REASON)),
            capture_output=True,
            text=True,
            check=False,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
        audit_reasons = [match["reason"] for match in json.loads(audit_run.stdout)["matches"]]
        assert len(set(audit_reasons)) == len(self._ONE_PATH_PER_AUDIT_REASON)
        longest_reason = max(audit_reasons, key=len)
        match_limit = self._limit("REVIEW_PR_AUDIT_MATCH_LINE_LIMIT")
        astral_path = "\U0001f600" * (self._limit("REVIEW_PR_AUDIT_PATH_ECHO_LIMIT") + 50)
        listed_matches = [(astral_path, longest_reason)] * match_limit
        unlisted_matches = [(astral_path, reason) for reason in audit_reasons]

        status, output = _call(
            "review_pr_audit_match_lines", self._stop_document(*listed_matches, *unlisted_matches)
        )

        assert status == 0
        # Bash output truncates above 30,000 decimal bytes (claude-skills/skills/subagent-delegation/REFERENCES.md).
        assert len(output.encode()) < 30_000
        assert output.splitlines()[-1].startswith("not shown: ")


class TestAuditMatchReport:
    _FIXED_LINE = "the audit reported {count} matched path(s), but its match list could not be formatted for display"

    def test_a_formattable_document_prints_the_same_lines_as_the_match_lines_helper(self):
        document = TestAuditMatchLines._stop_document(("a/CLAUDE.md", "loaded as instructions"))

        assert _call("review_pr_audit_match_report", document) == (0, '"a/CLAUDE.md": loaded as instructions')

    @pytest.mark.parametrize(
        ("unformattable_document", "expected_count"),
        [
            ('{"stop": true, "matches": [1, 2, 3]}', "3"),
            ('{"stop": true, "matches": "some raw text"}', "unknown"),
            ('{"stop": true}', "unknown"),
            ("not json", "unknown"),
            ("", "unknown"),
        ],
        ids=["non_object_entries", "matches_not_an_array", "no_matches_key", "not_json", "empty_stdout"],
    )
    def test_an_unformattable_document_prints_one_fixed_line_with_the_count_and_never_the_document(
        self, unformattable_document, expected_count
    ):
        status, output = _call("review_pr_audit_match_report", unformattable_document)

        assert (status, output) == (0, self._FIXED_LINE.format(count=expected_count))

    def test_a_document_with_a_formattable_entry_before_an_unformattable_one_prints_only_the_fixed_line(self):
        document = '{"stop": true, "matches": [{"path": "ok/CLAUDE.md", "reason": "reason"}, 3]}'

        assert _call("review_pr_audit_match_report", document) == (0, self._FIXED_LINE.format(count="2"))


class TestGhStatusDescription:
    def test_a_cap_kill_status_reads_as_a_timeout(self):
        assert _call("review_pr_gh_status_description", "124") == (0, "timed out")

    @pytest.mark.parametrize("status", ["1", "4", "127", "137", "143"])
    def test_every_other_status_reads_as_a_failure_naming_the_status(self, status):
        assert _call("review_pr_gh_status_description", status) == (0, f"failed (exit {status})")



class TestChecksTokenSource:
    """The override goes only to a PR whose url is on github.com; an empty override is no override."""

    @pytest.mark.parametrize(
        "pr_url,expected_with_token",
        [
            ("https://github.com/owner/repo/pull/7", "override"),
            ("https://GitHub.COM/owner/repo/pull/7", "override"),
            ("", "withheld"),
            ("https://octocat.ghe.com/owner/repo/pull/7", "withheld"),
            ("https://ghes.example.test/owner/repo/pull/7", "withheld"),
        ],
        ids=["github.com", "mixed-case-github.com", "url-absent", "ghe.com-tenant", "self-hosted-ghes"],
    )
    def test_a_non_empty_token_is_sent_only_to_a_github_com_pr(self, pr_url, expected_with_token):
        status, output = _call("review_pr_checks_token_source", pr_url, "override-token-value")
        assert (status, output) == (0, expected_with_token)

    @pytest.mark.parametrize(
        "lookalike_url",
        [
            "https://github.com.example.test/owner/repo/pull/7",
            "https://github.com:443/owner/repo/pull/7",
            "https://github.com@evil.test/owner/repo/pull/7",
            "https://api.github.com/owner/repo/pull/7",
            "https://notgithub.com/owner/repo/pull/7",
            "http://github.com/owner/repo/pull/7",
            "github.com/owner/repo/pull/7",
            " https://github.com/owner/repo/pull/7",
            "https://github.com",
        ],
        ids=[
            "suffix-domain", "port", "userinfo-smuggle", "github-subdomain", "prefix-lookalike", "plain-http",
            "no-scheme", "leading-space", "bare-host-without-path",
        ],
    )
    def test_a_lookalike_of_a_github_com_url_withholds_a_non_empty_token(self, lookalike_url):
        status, output = _call("review_pr_checks_token_source", lookalike_url, "override-token-value")
        assert (status, output) == (0, "withheld")

    @pytest.mark.parametrize(
        "pr_url",
        [
            "https://github.com/owner/repo/pull/7",
            "https://GitHub.COM/owner/repo/pull/7",
            "",
            "https://octocat.ghe.com/owner/repo/pull/7",
            "https://ghes.example.test/owner/repo/pull/7",
        ],
        ids=["github.com", "mixed-case-github.com", "url-absent", "ghe.com-tenant", "self-hosted-ghes"],
    )
    def test_an_empty_token_is_ambient_for_every_url(self, pr_url):
        status, output = _call("review_pr_checks_token_source", pr_url, "")
        assert (status, output) == (0, "ambient")


class TestResolveSessionAndPid:
    """_lib_resolve_claude_pid is stubbed after sourcing, so these rows pin the
    helper's own wiring without depending on the test process's ancestry."""

    @staticmethod
    def _resolve(stubbed_resolver_body: str, consequence: str = "", abort_note: str = "Abort before any fetch."):
        return _run_bash(
            f"_lib_resolve_claude_pid() {{ {stubbed_resolver_body}; }}\n"
            'status=0\n'
            'review_pr_resolve_session_and_pid "demo.sh" "$1" "$2" || status=$?\n'
            'printf "%s|%s|%s" "$status" "${SESSION_ID:-}" "${CLAUDE_PID:-}"',
            consequence,
            abort_note,
        )

    def test_a_resolved_session_sets_the_session_id_and_the_pid(self):
        result = self._resolve("printf 'sess-1 4242'")
        assert result.returncode == 0, result.stderr
        assert result.stdout == "0|sess-1|4242"
        assert result.stderr == ""

    def test_an_unresolvable_session_names_the_missing_hook_and_returns_one(self):
        result = self._resolve("return 1")
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith("1||")
        assert result.stderr == (
            "demo.sh: could not resolve this session's id (capture-session-id.sh SessionStart hook did not run). "
            "Abort before any fetch.\n"
        )

    def test_a_session_id_that_is_not_a_path_component_names_the_id_and_returns_one(self):
        result = self._resolve("printf '../escape 4242'")
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith("1|")
        assert result.stderr == (
            "demo.sh: resolved session id '../escape' is not a valid path component. Abort before any fetch.\n"
        )

    @pytest.mark.parametrize("stubbed_resolver_body", ["return 1", "printf '../escape 4242'"])
    def test_the_caller_supplied_consequence_and_abort_note_end_both_failure_messages(self, stubbed_resolver_body):
        result = self._resolve(
            stubbed_resolver_body,
            consequence=" -- cannot name the worktree",
            abort_note="Abort before creating a worktree.",
        )
        assert result.stderr.endswith(" -- cannot name the worktree. Abort before creating a worktree.\n")
