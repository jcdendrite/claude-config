"""Unit tests for evals/comment_ab_pool_count.py.

Every fixture is synthetic. The stage-gating tests inject fake git and GitHub sources, and the
git-reading tests build a throwaway repository, so no test touches the network or the checkout.

pyproject.toml adds 'evals' to the pytest pythonpath so `import comment_ab_pool_count` resolves.
"""

from __future__ import annotations

import json
import re
import subprocess
import textwrap
from pathlib import Path

import comment_ab_pool_count as pool
import pytest
from test_review_bench_mining import _commit, _git, _init_repo, _write

REPOSITORY = "owner/repo"


def _dedented(source: str) -> str:
    return textwrap.dedent(source).lstrip("\n")


class TestIssuesNamedIn:
    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("add the widget GH-12 (#40)", {12}),
            ("gh-12 lowercase prefix", {12}),
            ("Fixes #7: tighten the guard (#40)", {7}),
            ("RESOLVED #9 uppercase keyword", {9}),
            ("closes #3 and fixes #4", {3, 4}),
            ("GH-12 and GH-12 repeated", {12}),
            ("refactor the parser (#40)", set()),
            ("see #5 for context", set()),
            ("prefixes #5 without keyword", set()),
            ("GH-12abc is not an issue", set()),
            ("GH-\uff15 fullwidth digit", set()),
            ("Fixes #\u0663 arabic-indic digit", set()),
            ("GH-5 beside GH-\uff16", {5}),
        ],
    )
    def test_names_issues_by_prefix_or_closing_keyword(self, title, expected):
        assert pool.issues_named_in(title, pull_request_number=40) == expected

    @pytest.mark.parametrize(
        "keyword", ["close", "closes", "closed", "fix", "fixes", "fixed", "resolve", "resolves", "resolved"]
    )
    def test_names_the_issue_after_each_closing_keyword_spelling(self, keyword):
        assert pool.issues_named_in(f"{keyword} #7 in the parser", pull_request_number=40) == {7}

    @pytest.mark.parametrize("title", ["fixing #7 in the parser", "prefixed #7 in the parser", "closure #7 in the parser"])
    def test_does_not_name_an_issue_after_a_word_that_only_resembles_a_closing_keyword(self, title):
        assert pool.issues_named_in(title, pull_request_number=40) == frozenset()

    def test_squash_suffix_with_a_non_ascii_digit_is_not_a_pull_request_number(self):
        commit = _commit_record("c1", "add thing (#\uff14\uff10)", ["tool.py"])
        assert pool.pull_request_number(commit) is None

    def test_excludes_the_pull_requests_own_number(self):
        assert pool.issues_named_in("Fixes #40 (#40)", pull_request_number=40) == frozenset()

    def test_keeps_a_prefix_issue_beside_the_squash_suffix(self):
        assert pool.issues_named_in("GH-1502 harness (#1510)", pull_request_number=1510) == {1502}


class TestPromptIssueFor:
    def test_title_with_one_issue_wins_over_closing_references(self):
        issue = pool.prompt_issue_for("GH-5 thing (#9)", 9, [(REPOSITORY, 77)], REPOSITORY)
        assert issue == (REPOSITORY, 5)

    def test_title_with_two_issues_is_excluded_even_when_one_closing_reference_exists(self):
        assert pool.prompt_issue_for("GH-5 and GH-6 (#9)", 9, [(REPOSITORY, 77)], REPOSITORY) is None

    def test_untitled_issue_falls_back_to_the_single_closing_reference(self):
        assert pool.prompt_issue_for("thing (#9)", 9, [(REPOSITORY, 77)], REPOSITORY) == (REPOSITORY, 77)

    @pytest.mark.parametrize("closing", [[], [(REPOSITORY, 1), (REPOSITORY, 2)]])
    def test_untitled_pull_request_without_exactly_one_closing_reference_is_excluded(self, closing):
        assert pool.prompt_issue_for("thing (#9)", 9, closing, REPOSITORY) is None


class TestTestFileClassifier:
    @pytest.mark.parametrize(
        "path",
        [
            "claude/.claude/hooks/tests/test_x.py",
            "claude/.claude/hooks/tests/helper_module.py",
            "evals/fixtures/review-bench/sample.sh",
            "src/conftest.py",
            "src/test_thing.py",
            "tests/anything.sh",
        ],
    )
    def test_classifies_test_paths(self, path):
        assert pool.is_test_file(path)

    @pytest.mark.parametrize(
        "path",
        [
            "claude/.claude/scripts/tool.py",
            "src/contest.py",
            "src/tests.py",
            "src/testing/run.sh",
            "src/mytest_thing.py",
            "test_dir/run.sh",
        ],
    )
    def test_leaves_non_test_paths(self, path):
        assert not pool.is_test_file(path)

    def test_prefix_applies_only_to_python_modules(self):
        assert not pool.is_test_file("scripts/test_helper.sh")


class TestPreservedComments:
    @pytest.mark.parametrize(
        "comment",
        [
            "#!/usr/bin/env bash",
            "# shellcheck disable=SC2086",
            "# noqa: E501",
            "# NOQA",
            "# type: ignore[attr-defined]",
            "# fmt: off",
            "# hook-class: gate",
            "# tier-threat-model: cooperative",
            "# pragma: no cover",
        ],
    )
    def test_preserves_tool_and_header_comments(self, comment):
        assert pool.is_preserved_comment(comment, line_number=10)

    @pytest.mark.parametrize("line_number", [1, 2])
    def test_preserves_an_encoding_cookie_on_the_first_two_lines(self, line_number):
        assert pool.is_preserved_comment("# -*- coding: utf-8 -*-", line_number=line_number)

    def test_does_not_preserve_an_encoding_cookie_past_the_second_line(self):
        assert not pool.is_preserved_comment("# -*- coding: utf-8 -*-", line_number=3)

    @pytest.mark.parametrize(
        "comment",
        [
            "# the shellcheck rule needs this",
            "# silence noqa here because the import is lazy",
            "# a pragma of the compiler lives below",
            "# the type: annotation is deliberate",
            "# the fmt: marker is mentioned here",
            "# the hook-class: header is mentioned here",
            "# the tier-threat-model: line is mentioned here",
        ],
    )
    def test_does_not_preserve_prose_that_mentions_a_directive_mid_sentence(self, comment):
        assert not pool.is_preserved_comment(comment, line_number=10)

    def test_does_not_preserve_prose(self):
        assert not pool.is_preserved_comment("# keep this order because the caller indexes by position", line_number=10)


class TestFirstRevision:
    ISSUE = {"createdAt": "2026-01-02T03:04:05Z", "lastEditedAt": "2026-02-01T00:00:00Z", "body": "current body"}

    def test_unedited_issue_uses_its_body(self):
        issue = {**self.ISSUE, "lastEditedAt": None}
        assert pool.first_revision_from_graphql(issue, []) == pool.FirstRevision("current body", pool.FIRST_REVISION_UNEDITED)

    def test_oldest_edit_at_creation_time_holds_the_original(self):
        edits = [
            {"editedAt": "2026-02-01T00:00:00Z", "diff": "newest"},
            {"editedAt": "2026-01-02T03:04:05Z", "diff": "original"},
            {"editedAt": "2026-01-15T00:00:00Z", "diff": "middle"},
        ]
        revision = pool.first_revision_from_graphql(self.ISSUE, edits)
        assert revision == pool.FirstRevision("original", pool.FIRST_REVISION_OLDEST_EDIT)

    def test_oldest_edit_after_creation_is_unrecoverable(self):
        edits = [{"editedAt": "2026-01-02T03:04:06Z", "diff": "later"}]
        assert pool.first_revision_from_graphql(self.ISSUE, edits).reason == pool.FIRST_REVISION_UNRECOVERABLE

    def test_edited_issue_without_edit_nodes_is_unrecoverable(self):
        assert pool.first_revision_from_graphql(self.ISSUE, []).reason == pool.FIRST_REVISION_UNRECOVERABLE

    def test_oldest_edit_with_null_diff_is_unrecoverable(self):
        edits = [{"editedAt": "2026-01-02T03:04:05Z", "diff": None}]
        assert pool.first_revision_from_graphql(self.ISSUE, edits).reason == pool.FIRST_REVISION_UNRECOVERABLE

    def test_timestamps_compare_as_instants_not_strings(self):
        edits = [{"editedAt": "2026-01-02T03:04:05+00:00", "diff": "original"}]
        assert pool.first_revision_from_graphql(self.ISSUE, edits).text == "original"


class TestNamesPullRequestBranchOrCommit:
    @staticmethod
    def _names(text, head_ref=None, known_commits=frozenset()):
        return pool.names_pull_request_branch_or_commit(
            text, 40, REPOSITORY, head_ref, lambda candidate: candidate in known_commits
        )

    @pytest.mark.parametrize(
        "text",
        [
            "see #40 for the fix",
            f"https://github.com/{REPOSITORY}/pull/40",
            f"https://github.com/{REPOSITORY}/pull/40/files",
            f"https://GitHub.com/{REPOSITORY.upper()}/pull/40",
            "PR (#40)",
        ],
    )
    def test_detects_the_pull_request(self, text):
        assert self._names(text)

    @pytest.mark.parametrize(
        "text",
        [
            "see #400 and #4 and pull/401",
            f"https://github.com/{REPOSITORY}/pull/401",
            "https://github.com/other/repo/pull/40",
            f"https://github.com/{REPOSITORY}sitory/pull/40",
            "see /pull/40 for context",
        ],
        ids=["other-numbers", "longer-number", "other-repository", "longer-repository-name", "unqualified-link"],
    )
    def test_ignores_a_reference_that_is_not_this_repositorys_pull_request(self, text):
        assert not self._names(text)

    def test_detects_the_branch_name_as_a_whole_token(self):
        assert self._names("work lives on GH-9/some-branch today", head_ref="GH-9/some-branch")

    @pytest.mark.parametrize("head_ref", ["feat-x", "feat_x", "x1", "area/x"])
    def test_detects_a_branch_name_with_a_separator_or_digit(self, head_ref):
        assert self._names(f"work lives on {head_ref} today", head_ref=head_ref)

    @pytest.mark.parametrize("head_ref", ["main", "fix", "docs"])
    def test_ignores_a_bare_common_word_branch_that_the_text_uses_as_a_word(self, head_ref):
        assert not self._names(f"update the {head_ref} page for the {head_ref} case", head_ref=head_ref)

    @pytest.mark.parametrize(
        "text",
        [
            "the fix-items effort",
            "the pre-fix-it effort",
            "the fix-it/more effort",
            "the fix-it-2 effort",
            "the xfix-it effort",
            "the other/fix-it effort",
            "the v1.fix-it effort",
        ],
        ids=[
            "word-after",
            "hyphen-before",
            "slash-after",
            "hyphen-after",
            "word-character-before",
            "slash-before",
            "dot-before",
        ],
    )
    def test_ignores_a_branch_name_that_is_only_a_substring(self, text):
        assert not self._names(text, head_ref="fix-it")

    @pytest.mark.parametrize(
        ("head_ref", "text", "expected"),
        [
            ("feat-(x)+y", "work lives on feat-(x)+y today", True),
            ("feat-(x", "work lives on feat-(x today", True),
            ("a.b-1", "keep a.b-1 around", True),
            ("a.b-1", "keep aXb-1 around", False),
            ("fix[1]", "keep fix1 around", False),
        ],
        ids=[
            "parens-and-plus",
            "unbalanced-paren",
            "dot-literal",
            "dot-is-not-a-wildcard",
            "brackets-are-not-a-class",
        ],
    )
    def test_matches_a_branch_name_literally_even_with_regex_metacharacters(self, head_ref, text, expected):
        assert self._names(text, head_ref=head_ref) is expected

    def test_detects_a_real_commit_abbreviation(self):
        assert self._names("regressed in abc1234 earlier", known_commits={"abc1234"})

    def test_ignores_hex_looking_words_and_numbers_that_are_no_commit(self):
        assert not self._names("a decade ago, deadbee, 1234567 items", known_commits=set())

    def test_does_not_probe_an_all_digit_token(self):
        probed = []

        def record_probe(candidate):
            probed.append(candidate)
            return False

        pool.names_pull_request_branch_or_commit("1234567 items", 40, REPOSITORY, None, record_probe)
        assert probed == []

    def test_does_not_probe_a_letter_only_token_that_is_long_enough_to_look_like_a_commit(self):
        probed = []

        def record_probe(candidate):
            probed.append(candidate)
            return True

        assert not pool.names_pull_request_branch_or_commit("fixed in defaced", 40, REPOSITORY, None, record_probe)
        assert probed == []


class TestPythonRegions:
    def test_function_with_a_comment_holds_one(self):
        source = _dedented(
            """
            def documented():
                # explains why
                return 1

            def bare():
                return 2
            """
        )
        by_start = {region.touch_start: region.holds_comment for region in pool.python_regions(source)}
        assert by_start == {1: True, 5: False}

    def test_docstring_counts_as_a_comment(self):
        source = _dedented(
            '''
            def documented():
                """Says what it does."""
                return 1
            '''
        )
        assert [region.holds_comment for region in pool.python_regions(source)] == [True]

    def test_comment_directly_above_a_decorator_counts_as_inside(self):
        source = _dedented(
            """
            # why this decorator is needed
            @staticmethod
            def decorated():
                return 1
            """
        )
        (region,) = pool.python_regions(source)
        assert (region.touch_start, region.last_line, region.holds_comment) == (1, 4, True)

    def test_stack_of_comments_above_a_function_starts_the_region_at_the_top_comment(self):
        source = _dedented(
            """
            # first note
            # second note
            def stacked():
                return 1
            """
        )
        (region,) = pool.python_regions(source)
        assert (region.touch_start, region.last_line, region.holds_comment) == (1, 4, True)

    def test_trailing_comment_after_a_statement_counts(self):
        source = "def f():\n    value = 1  # why this value\n    return value\n"
        assert [region.holds_comment for region in pool.python_regions(source)] == [True]

    def test_comment_on_the_last_line_of_a_function_counts(self):
        source = "def f():\n    value = 1\n    return value  # why this is returned\n"
        (region,) = pool.python_regions(source)
        assert (region.last_line, region.holds_comment) == (3, True)

    def test_async_function_is_a_region(self):
        source = _dedented(
            """
            async def fetch():
                # why it awaits here
                return 1
            """
        )
        (region,) = pool.python_regions(source)
        assert (region.touch_start, region.last_line, region.holds_comment) == (1, 3, True)

    def test_diff_touching_only_the_comment_above_a_function_touches_it(self):
        source = "# why this helper exists\ndef f():\n    return 1\n"
        result = pool.judge_why_comment([("M", "tool.py")], {"tool.py": {1}}, {"tool.py": source})
        assert result.kept

    def test_comment_above_a_blank_line_is_not_directly_above(self):
        source = _dedented(
            """
            # about the module

            def bare():
                return 1
            """
        )
        assert [region.holds_comment for region in pool.python_regions(source)] == [False]

    def test_preserved_comment_alone_does_not_count(self):
        source = _dedented(
            """
            def pragma_only():
                return 1  # noqa: E501
            """
        )
        assert [region.holds_comment for region in pool.python_regions(source)] == [False]

    def test_prose_comment_containing_coding_inside_a_function_counts(self):
        source = "import json\n\n\ndef f():\n    # canonical encoding: sorted keys, compact separators\n    return 1\n"
        assert [region.holds_comment for region in pool.python_regions(source)] == [True]

    @pytest.mark.parametrize(
        "source",
        [
            "# -*- coding: utf-8 -*-\ndef f():\n    return 1\n",
            "#!/usr/bin/env python3\n# -*- coding: utf-8 -*-\ndef f():\n    return 1\n",
        ],
        ids=["cookie-on-line-1", "cookie-on-line-2"],
    )
    def test_encoding_cookie_on_the_first_two_lines_does_not_count(self, source):
        assert [region.holds_comment for region in pool.python_regions(source)] == [False]

    def test_method_is_its_own_region_and_class_body_adds_none(self):
        source = _dedented(
            """
            class Holder:
                limit = 3  # a class attribute comment

                def method(self):
                    return self.limit
            """
        )
        regions = pool.python_regions(source)
        assert [(region.touch_start, region.holds_comment) for region in regions] == [(4, False)]

    def test_nested_function_makes_the_outer_region_hold_its_comment(self):
        source = _dedented(
            """
            def outer():
                def inner():
                    # why inner works this way
                    return 1
                return inner()
            """
        )
        assert {region.touch_start: region.holds_comment for region in pool.python_regions(source)} == {1: True, 2: True}

    def test_hash_inside_a_string_is_no_comment(self):
        source = 'def f():\n    return "# not a comment"\n'
        assert [region.holds_comment for region in pool.python_regions(source)] == [False]

    def test_returns_none_for_source_that_does_not_parse(self):
        assert pool.python_regions("def broken(:\n") is None


class TestShellSourceHoldsComment:
    @pytest.mark.parametrize(
        "source",
        [
            "f() {\n  # why the retry exists\n  run_it\n}\n",
            "#!/usr/bin/env bash\n# why this script exists\nrun_it\n",
            "run_it\n\n\nother_it\n# a comment far from the top\n",
            "f() {\n  run_it  # why it runs\n}\n",
            "f() {\n  run_it;# why after a semicolon\n}\n",
            "# shellcheck disable=SC2086\nrun $x\n",
            "run_it\n#!/bin/sh\n",
            "f() {\n  echo 'see # inside a quote'\n}\n",
            "f() {\n  run_it &# why in the background\n}\n",
            "f() {\n  run_it |# why piped\n}\n",
            "(# why in a subshell\n  run_it\n)\n",
        ],
        ids=[
            "inside-a-function",
            "after-a-shebang",
            "far-from-the-top",
            "trailing-comment",
            "comment-after-a-semicolon",
            "directive-comment",
            "shebang-text-on-a-later-line",
            "hash-inside-a-quote-is-over-kept",
            "comment-after-an-ampersand",
            "comment-after-a-pipe",
            "comment-after-an-open-parenthesis",
        ],
    )
    def test_any_comment_candidate_besides_a_first_line_shebang_counts(self, source):
        assert pool.shell_source_holds_comment(source)

    @pytest.mark.parametrize(
        "source",
        [
            "",
            "#!/usr/bin/env bash\nrun_it\n",
            "f() {\n  run_it\n}\n",
            "count=${#items[@]}\nlength=$#\nbase=16#ff\ntrimmed=${path#prefix}\n",
        ],
        ids=["empty", "only-a-shebang", "no-comment", "hash-that-is-not-after-whitespace-or-a-control-character"],
    )
    def test_source_with_no_comment_or_only_a_shebang_does_not_count(self, source):
        assert not pool.shell_source_holds_comment(source)


class TestHasShellOrPythonShebang:
    @pytest.mark.parametrize(
        "source",
        [
            "#!/bin/sh\nrun_it\n",
            "#!/usr/bin/env bash\n",
            "#!/usr/bin/env -S python3 -u\n",
            "#!/usr/bin/env PYTHONPATH=. python3\n",
            "#!/usr/bin/python3.12\n",
            "#! /bin/zsh\n",
            "#!/bin/bash\r\nrun_it\r\n",
        ],
        ids=["sh", "env-bash", "env-with-a-flag", "env-with-a-variable", "versioned-python", "space-after-the-marker", "crlf"],
    )
    def test_recognizes_a_shell_or_python_interpreter(self, source):
        assert pool.has_shell_or_python_shebang(source)

    # Literal names, not SHELL_INTERPRETERS, so dropping one from the set fails its own case.
    @pytest.mark.parametrize("interpreter", ["sh", "bash", "dash", "zsh", "ksh"])
    def test_recognizes_each_of_the_five_named_shells(self, interpreter):
        assert pool.has_shell_or_python_shebang(f"#!/bin/{interpreter}\n")

    @pytest.mark.parametrize(
        "source",
        [
            "",
            "x: int\n",
            "#!/usr/bin/env node\n",
            "#!/usr/bin/perl\n",
            "#!\n",
            "#!/usr/bin/env\n",
            "run_it\n#!/bin/sh\n",
            "# a comment\n",
            "#!/usr/bin/python3-config\n",
            "#!/usr/bin/bash-completion\n",
        ],
        ids=[
            "empty",
            "no-shebang",
            "node",
            "perl",
            "bare-marker",
            "bare-env",
            "shebang-on-a-later-line",
            "comment",
            "name-only-starts-with-python",
            "name-only-starts-with-a-shell",
        ],
    )
    def test_rejects_any_other_first_line(self, source):
        assert not pool.has_shell_or_python_shebang(source)


class TestParseChangedBaseLines:
    def test_reads_modified_ranges_and_counts_insertions_at_both_neighbors(self):
        diff = _dedented(
            """
            diff --git a/m.py b/m.py
            --- a/m.py
            +++ b/m.py
            @@ -3,2 +3,2 @@
            -old a
            -old b
            +new a
            +new b
            @@ -10,0 +11 @@
            +inserted
            """
        )
        assert pool.parse_changed_base_lines(diff) == {"m.py": {3, 4, 10, 11}}

    def test_pure_deletion_touches_the_removed_base_lines(self):
        diff = "--- a/m.py\n+++ b/m.py\n@@ -5,2 +4,0 @@\n-a\n-b\n"
        assert pool.parse_changed_base_lines(diff) == {"m.py": {5, 6}}

    def test_no_newline_marker_is_not_read_as_a_changed_line(self):
        diff = "--- a/m.py\n+++ b/m.py\n@@ -3 +3 @@\n-old\n\\ No newline at end of file\n+new\n\\ No newline at end of file\n"
        assert pool.parse_changed_base_lines(diff) == {"m.py": {3}}

    def test_path_git_quotes_is_skipped(self):
        diff = '--- "a/caf\\303\\251.py"\n+++ "b/caf\\303\\251.py"\n@@ -2 +2 @@\n-x\n+y\n'
        assert pool.parse_changed_base_lines(diff) == {}

    def test_deleted_file_touches_every_base_line(self):
        diff = "--- a/gone.sh\n+++ /dev/null\n@@ -1,3 +0,0 @@\n-a\n-b\n-c\n"
        assert pool.parse_changed_base_lines(diff) == {"gone.sh": {1, 2, 3}}

    def test_added_file_has_no_base_lines(self):
        diff = "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1,2 @@\n+a\n+b\n"
        assert pool.parse_changed_base_lines(diff) == {}

    def test_removed_line_that_looks_like_a_header_is_not_read_as_one(self):
        diff = "--- a/m.sh\n+++ b/m.sh\n@@ -4 +4 @@\n--- a/other.sh\n+++ b/other.sh\n"
        assert pool.parse_changed_base_lines(diff) == {"m.sh": {4}}

    def test_form_feed_inside_a_removed_line_does_not_split_it(self):
        # Content that would read as extra removed lines and a hunk header if the form feeds ended lines.
        diff = (
            "--- a/m.py\n+++ b/m.py\n@@ -3,2 +2,0 @@\n-a\x0c-b\x0c@@ -90 +90 @@\n-c\n"
            "--- a/n.py\n+++ b/n.py\n@@ -8 +8 @@\n-x\n+y\n"
        )
        assert pool.parse_changed_base_lines(diff) == {"m.py": {3, 4}, "n.py": {8}}

    def test_single_line_hunk_header_omits_the_count(self):
        diff = "--- a/m.py\n+++ b/m.py\n@@ -7 +7 @@\n-x\n+y\n"
        assert pool.parse_changed_base_lines(diff) == {"m.py": {7}}


class TestJudgeWhyComment:
    COMMENTED = "def f():\n    # why\n    return 1\n"
    BARE = "def f():\n    return 1\n"

    def test_keeps_a_task_whose_touched_function_holds_a_comment(self):
        result = pool.judge_why_comment([("M", "tool.py")], {"tool.py": {3}}, {"tool.py": self.COMMENTED})
        assert result == pool.WhyCommentResult(kept=True, new_files_only=False)

    def test_drops_a_task_whose_touched_function_holds_no_comment(self):
        result = pool.judge_why_comment([("M", "tool.py")], {"tool.py": {2}}, {"tool.py": self.BARE})
        assert result == pool.WhyCommentResult(kept=False, new_files_only=False)

    def test_ignores_a_commented_function_the_diff_does_not_touch(self):
        source = self.COMMENTED + "\n\ndef g():\n    return 2\n"
        result = pool.judge_why_comment([("M", "tool.py")], {"tool.py": {6}}, {"tool.py": source})
        assert not result.kept

    @pytest.mark.parametrize(
        "changed_line",
        [1, 5, 11],
        ids=["module-constant-above-every-region", "class-attribute-above-a-method", "class-attribute-between-methods"],
    )
    def test_drops_a_python_change_outside_every_function_even_when_a_commented_function_sits_nearby(self, changed_line):
        source = (
            "LIMIT = 3\n\n\nclass Holder:\n    limit = 4\n\n    def commented(self):\n        # why\n        return 1\n\n"
            "    other = 5\n\n    def bare(self):\n        return 2\n"
        )
        result = pool.judge_why_comment([("M", "tool.py")], {"tool.py": {changed_line}}, {"tool.py": source})
        assert result == pool.WhyCommentResult(kept=False, new_files_only=False)

    def test_reads_a_changed_test_file_region_too(self):
        changes = [("M", "tool.py"), ("M", "tests/test_tool.py")]
        result = pool.judge_why_comment(
            changes,
            {"tool.py": {2}, "tests/test_tool.py": {3}},
            {"tool.py": self.BARE, "tests/test_tool.py": self.COMMENTED},
        )
        assert result.kept

    def test_excludes_a_task_whose_non_test_changes_are_all_new_files(self):
        changes = [("A", "tool.py"), ("M", "tests/test_tool.py")]
        result = pool.judge_why_comment(changes, {"tests/test_tool.py": {3}}, {"tests/test_tool.py": self.COMMENTED})
        assert result == pool.WhyCommentResult(kept=False, new_files_only=True)

    def test_one_modified_non_test_file_beside_a_new_one_is_not_new_files_only(self):
        changes = [("A", "new.py"), ("M", "old.py")]
        result = pool.judge_why_comment(changes, {"old.py": {3}}, {"old.py": self.COMMENTED})
        assert result.kept

    def test_counts_a_base_file_that_does_not_parse_beside_a_commented_region(self):
        changes = [("M", "broken.py"), ("M", "run.sh")]
        result = pool.judge_why_comment(
            changes,
            {"broken.py": {1}, "run.sh": {2}},
            {"broken.py": "def broken(:\n", "run.sh": "f() {\n  # why\n  :\n}\n"},
        )
        assert result == pool.WhyCommentResult(kept=True, new_files_only=False, unparsable_files=1)

    @pytest.mark.parametrize("unparsable_first", [True, False], ids=["unparsable-file-first", "commented-file-first"])
    def test_verdict_and_unparsable_count_do_not_depend_on_the_order_of_touched_files(self, unparsable_first):
        changes = [("M", "broken.py"), ("M", "tool.py")]
        touched = [("broken.py", {1}), ("tool.py", {3})]
        sources = {"broken.py": "def broken(:\n", "tool.py": self.COMMENTED}
        ordered = dict(touched if unparsable_first else reversed(touched))
        result = pool.judge_why_comment(changes, ordered, sources)
        assert result == pool.WhyCommentResult(kept=True, new_files_only=False, unparsable_files=1)

    def test_keeps_a_task_whose_only_touched_file_does_not_parse(self):
        result = pool.judge_why_comment([("M", "broken.py")], {"broken.py": {1}}, {"broken.py": "def broken(:\n"})
        assert result == pool.WhyCommentResult(kept=True, new_files_only=False, unparsable_files=1)

    def test_keeps_a_task_with_an_unparsable_file_beside_a_commentless_region(self):
        changes = [("M", "broken.py"), ("M", "tool.py")]
        result = pool.judge_why_comment(
            changes,
            {"broken.py": {1}, "tool.py": {2}},
            {"broken.py": "def broken(:\n", "tool.py": self.BARE},
        )
        assert result == pool.WhyCommentResult(kept=True, new_files_only=False, unparsable_files=1)

    def test_keeps_a_shell_change_far_from_the_only_comment_in_the_file(self):
        source = "# why the first function exists\nfirst() {\n  run_it\n}\n\nsecond() {\n  other_it\n}\n"
        result = pool.judge_why_comment([("M", "run.sh")], {"run.sh": {7}}, {"run.sh": source})
        assert result == pool.WhyCommentResult(kept=True, new_files_only=False)

    @pytest.mark.parametrize(
        "source",
        ["f() {\n  run_it\n}\n", "#!/usr/bin/env bash\nf() {\n  run_it\n}\n"],
        ids=["no-comment", "only-a-shebang"],
    )
    def test_drops_a_shell_change_when_the_base_file_holds_no_comment_besides_a_shebang(self, source):
        result = pool.judge_why_comment([("M", "run.sh")], {"run.sh": {2}}, {"run.sh": source})
        assert result == pool.WhyCommentResult(kept=False, new_files_only=False)

    @pytest.mark.parametrize("path", ["tool.py", "run.sh"])
    def test_keeps_and_counts_a_modified_file_whose_diff_has_no_hunks(self, path):
        result = pool.judge_why_comment([("M", path)], {}, {})
        assert result == pool.WhyCommentResult(kept=True, new_files_only=False, unparsable_files=1)

    def test_counts_a_hunkless_file_beside_a_judged_commentless_one(self):
        changes = [("M", "binary.py"), ("M", "tool.py")]
        result = pool.judge_why_comment(changes, {"tool.py": {2}}, {"tool.py": self.BARE})
        assert result == pool.WhyCommentResult(kept=True, new_files_only=False, unparsable_files=1)

    def test_does_not_count_a_new_file_as_hunkless(self):
        changes = [("A", "new.py"), ("M", "old.py")]
        result = pool.judge_why_comment(changes, {"old.py": {2}}, {"old.py": self.BARE})
        assert result == pool.WhyCommentResult(kept=False, new_files_only=False)

    def test_counts_a_hunkless_test_file_too(self):
        changes = [("M", "tool.py"), ("M", "tests/test_tool.py")]
        result = pool.judge_why_comment(changes, {"tool.py": {2}}, {"tool.py": self.BARE})
        assert result == pool.WhyCommentResult(kept=True, new_files_only=False, unparsable_files=1)

    @pytest.mark.parametrize(
        "source",
        ["#!/usr/bin/env python3\ndef f():\n    return 1\n", "#!/bin/bash\nrun_it\n"],
        ids=["python-shebang", "shell-shebang"],
    )
    def test_keeps_and_counts_a_modified_script_without_a_python_or_shell_suffix(self, source):
        changes = [("M", "tool.py"), ("M", "bin/run-tool")]
        result = pool.judge_why_comment(changes, {"tool.py": {2}}, {"tool.py": self.BARE, "bin/run-tool": source})
        assert result == pool.WhyCommentResult(kept=True, new_files_only=False, unparsable_files=1)

    def test_a_modified_script_without_a_suffix_keeps_a_task_whose_suffix_files_are_all_new(self):
        changes = [("A", "new.py"), ("M", "bin/run-tool")]
        result = pool.judge_why_comment(changes, {}, {"bin/run-tool": "#!/bin/sh\nrun_it\n"})
        assert result == pool.WhyCommentResult(kept=True, new_files_only=False, unparsable_files=1)

    def test_a_new_script_without_a_suffix_does_not_keep_a_task_whose_suffix_files_are_all_new(self):
        changes = [("A", "new.py"), ("A", "bin/run-tool")]
        result = pool.judge_why_comment(changes, {}, {"bin/run-tool": "#!/bin/sh\nrun_it\n"})
        assert result == pool.WhyCommentResult(kept=False, new_files_only=True)

    @pytest.mark.parametrize(
        ("path", "source"),
        [
            ("stubs/tool.pyi", "x: int\n"),
            ("scripts/run.bash", "run_it\n"),
            ("docs/page.md", "# Title\n"),
            ("bin/data-file", "plain text\n"),
            ("bin/node-tool", "#!/usr/bin/env node\n"),
        ],
        ids=["pyi", "bash-suffix", "markdown", "extensionless-text", "other-interpreter"],
    )
    def test_drops_a_task_whose_other_touched_files_are_no_shell_or_python_script(self, path, source):
        changes = [("M", "tool.py"), ("M", path)]
        result = pool.judge_why_comment(changes, {"tool.py": {2}}, {"tool.py": self.BARE, path: source})
        assert result == pool.WhyCommentResult(kept=False, new_files_only=False)

    def test_keeps_and_counts_a_modified_file_without_a_suffix_whose_base_blob_is_missing(self):
        changes = [("M", "tool.py"), ("M", "bin/missing-blob")]
        result = pool.judge_why_comment(changes, {"tool.py": {2}}, {"tool.py": self.BARE, "bin/missing-blob": None})
        assert result == pool.WhyCommentResult(kept=True, new_files_only=False, unparsable_files=1)

    def test_keeps_and_counts_a_deleted_script_without_a_python_or_shell_suffix(self):
        changes = [("M", "tool.py"), ("D", "bin/run-tool")]
        base_sources = {"tool.py": self.BARE, "bin/run-tool": "#!/bin/sh\nrun_it\n"}
        result = pool.judge_why_comment(changes, {"tool.py": {2}}, base_sources)
        assert result == pool.WhyCommentResult(kept=True, new_files_only=False, unparsable_files=1)

    @pytest.mark.parametrize("path", ["tool.py", "run.sh"])
    def test_keeps_and_counts_a_changed_file_missing_at_the_base(self, path):
        result = pool.judge_why_comment([("M", path)], {path: {1}}, {path: None})
        assert result == pool.WhyCommentResult(kept=True, new_files_only=False, unparsable_files=1)


def _commit_record(sha, subject, paths, *, parents=("base",), statuses=None):
    statuses = statuses or {}
    return pool.CommitRecord(sha, tuple(parents), subject, tuple((statuses.get(path, "M"), path) for path in paths))


class _FakeSources:
    """Stage-gating collaborators. Each argument lists what the matching real source would return."""

    def __init__(self, pull_requests=None, revisions=None, commits_before_study=None, known_commits=(), judged=None):
        self.pull_requests = pull_requests or {}
        self.revisions = revisions or {}
        self.commits_before_study = commits_before_study
        self.known_commits = set(known_commits)
        self.judged = judged or {}
        self.requested_issue_numbers = None

    def build(self):
        def fetch_first_revisions(numbers):
            self.requested_issue_numbers = list(numbers)
            return {number: self.revisions[number] for number in numbers if number in self.revisions}

        return pool.PoolSources(
            repository=REPOSITORY,
            base_predates_study=lambda base: self.commits_before_study is None or base in self.commits_before_study,
            fetch_pull_requests=lambda numbers: {n: self.pull_requests[n] for n in numbers if n in self.pull_requests},
            fetch_first_revisions=fetch_first_revisions,
            is_commit=lambda candidate: candidate in self.known_commits,
            judge_why_comment=self._judge,
        )

    def _judge(self, commit):
        verdict = self.judged.get(commit.sha, pool.WhyCommentResult(kept=True, new_files_only=False))
        assert verdict.kept or verdict.unparsable_files == 0, "the real judge keeps every task with an unparsable file"
        assert not verdict.new_files_only or (not verdict.kept and verdict.unparsable_files == 0), (
            "the real judge never reports a new-files-only task as kept or with an unparsable file"
        )
        return verdict


def _recovered(text="a prompt"):
    return pool.FirstRevision(text, pool.FIRST_REVISION_UNEDITED)


def _counts(ceiling):
    return {stage.id: stage.count for stage in ceiling.stages}


class TestComputeCeiling:
    CODE = "claude/.claude/scripts/tool.py"

    def test_passing_commit_survives_every_stage(self):
        commits = [_commit_record("c1", "GH-5 add the thing (#40)", [self.CODE])]
        sources = _FakeSources(revisions={5: _recovered()})
        ceiling = pool.compute_ceiling(commits, sources.build())
        assert _counts(ceiling) == {"1": 1, "2": 1, "3": 1, "4": 1, "5": 1, "6": 1, "7": 1}
        assert ceiling.surviving_pull_requests == (40,)
        assert ceiling.ceiling == 1

    def test_ceiling_is_the_last_stage_count_when_every_stage_shrinks(self):
        commits = [
            _commit_record("survivor", "GH-5 survives (#40)", [self.CODE]),
            _commit_record("commentless", "GH-5 no comment (#41)", [self.CODE]),
            _commit_record("names-pr", "GH-6 names its pr (#42)", [self.CODE]),
            _commit_record("late-base", "GH-5 late base (#43)", [self.CODE], parents=("new-base",)),
            _commit_record("env", "GH-5 environment (#44)", [self.CODE, "pyproject.toml"]),
            _commit_record("docs-only", "GH-5 docs (#45)", ["docs/x.md"]),
            _commit_record("no-issue", "no issue named (#46)", [self.CODE]),
        ]
        sources = _FakeSources(
            revisions={5: _recovered(), 6: _recovered("the fix is in #42")},
            commits_before_study={"base"},
            judged={"commentless": pool.WhyCommentResult(kept=False, new_files_only=False)},
        )
        ceiling = pool.compute_ceiling(commits, sources.build())
        counts = _counts(ceiling)
        assert counts == {"1": 7, "2": 6, "3": 5, "4": 4, "5": 3, "6": 2, "7": 1}
        assert ceiling.ceiling == len(ceiling.surviving_pull_requests) == counts["7"]
        assert ceiling.ceiling != counts["6"]

    def test_stage_one_drops_merge_commits_root_commits_and_missing_suffixes(self):
        commits = [
            _commit_record("merge", "Merge branch x (#41)", [self.CODE], parents=("p1", "p2")),
            _commit_record("root", "initial (#42)", [self.CODE], parents=()),
            _commit_record("plain", "GH-5 no suffix", [self.CODE]),
            _commit_record("ok", "GH-5 thing (#43)", [self.CODE]),
        ]
        ceiling = pool.compute_ceiling(commits, _FakeSources(revisions={5: _recovered()}).build())
        assert _counts(ceiling)["1"] == 1

    def test_stage_two_uses_closing_references_when_the_title_names_none(self):
        commits = [
            _commit_record("a", "thing (#40)", [self.CODE]),
            _commit_record("b", "other (#41)", [self.CODE]),
            _commit_record("c", "third (#42)", [self.CODE]),
        ]
        sources = _FakeSources(
            pull_requests={
                40: pool.PullRequestInfo(None, ((REPOSITORY, 5),), "thing (#40)"),
                41: pool.PullRequestInfo(None, ((REPOSITORY, 5), (REPOSITORY, 6)), "other (#41)"),
                42: pool.PullRequestInfo(None, (), "third (#42)"),
            },
            revisions={5: _recovered()},
        )
        assert _counts(pool.compute_ceiling(commits, sources.build()))["2"] == 1

    def test_stage_two_reads_the_pull_request_title_not_the_commit_subject(self):
        commits = [_commit_record("titled", "rework the thing (#40)", [self.CODE])]
        sources = _FakeSources(
            pull_requests={40: pool.PullRequestInfo(None, (), "GH-5 rework the thing")}, revisions={5: _recovered()}
        )
        ceiling = pool.compute_ceiling(commits, sources.build())
        assert _counts(ceiling)["2"] == 1
        assert sources.requested_issue_numbers == [5]

    def test_stage_two_drops_a_pull_request_whose_title_names_no_issue_though_the_commit_subject_does(self):
        commits = [_commit_record("retitled", "GH-5 rework the thing (#40)", [self.CODE])]
        sources = _FakeSources(pull_requests={40: pool.PullRequestInfo(None, (), "rework the thing")})
        assert _counts(pool.compute_ceiling(commits, sources.build()))["2"] == 0

    def test_stage_two_reads_the_commit_subject_when_github_holds_no_such_pull_request(self):
        commits = [_commit_record("unlisted", "GH-5 rework the thing (#40)", [self.CODE])]
        sources = _FakeSources(revisions={5: _recovered()})
        assert _counts(pool.compute_ceiling(commits, sources.build()))["2"] == 1

    def test_stages_three_and_four_gate_on_changed_paths(self):
        commits = [
            _commit_record("only-test", "GH-5 a (#40)", ["tests/test_a.py"]),
            _commit_record("only-docs", "GH-5 b (#41)", ["docs/x.md"]),
            _commit_record("env", "GH-5 c (#42)", [self.CODE, "pyproject.toml"]),
            _commit_record("ok", "GH-5 d (#43)", [self.CODE, ".github/workflows/other.yml"]),
        ]
        counts = _counts(pool.compute_ceiling(commits, _FakeSources(revisions={5: _recovered()}).build()))
        assert (counts["2"], counts["3"], counts["4"]) == (4, 2, 1)

    @pytest.mark.parametrize(
        "path",
        ["claude/.local/bin/tool", "stubs/tool.pyi", "scripts/run.bash"],
        ids=["extensionless-script", "pyi", "bash-suffix"],
    )
    def test_stage_three_matches_by_suffix_so_a_script_named_otherwise_is_no_code_change(self, path):
        commits = [_commit_record("other-suffix", "GH-5 a (#40)", [path]), _commit_record("ok", "GH-5 b (#41)", [self.CODE])]
        counts = _counts(pool.compute_ceiling(commits, _FakeSources(revisions={5: _recovered()}).build()))
        assert (counts["2"], counts["3"]) == (2, 1)

    @pytest.mark.parametrize("environment_file", ["requirements-dev.txt", "pyproject.toml", ".github/workflows/tests.yml"])
    def test_stage_four_drops_a_change_to_each_environment_file_but_keeps_a_neighboring_workflow(self, environment_file):
        commits = [
            _commit_record("env", "GH-5 environment (#40)", [self.CODE, environment_file]),
            _commit_record("neighbor", "GH-5 other workflow (#41)", [self.CODE, ".github/workflows/other.yml"]),
        ]
        ceiling = pool.compute_ceiling(commits, _FakeSources(revisions={5: _recovered()}).build())
        assert (_counts(ceiling)["3"], _counts(ceiling)["4"]) == (2, 1)
        assert ceiling.surviving_pull_requests == (41,)

    def test_stage_five_drops_a_base_that_does_not_predate_the_study(self):
        commits = [
            _commit_record("early", "GH-5 a (#40)", [self.CODE], parents=("old-base",)),
            _commit_record("late", "GH-5 b (#41)", [self.CODE], parents=("new-base",)),
        ]
        sources = _FakeSources(revisions={5: _recovered()}, commits_before_study={"old-base"})
        assert _counts(pool.compute_ceiling(commits, sources.build()))["5"] == 1

    def test_stage_six_drops_only_a_recovered_first_revision_that_names_a_reference_and_counts_the_unrecovered(self):
        commits = [
            _commit_record("unrecoverable", "GH-1 a (#40)", [self.CODE]),
            _commit_record("missing", "GH-2 b (#41)", [self.CODE]),
            _commit_record("names-pr", "GH-3 c (#42)", [self.CODE]),
            _commit_record("names-branch", "GH-4 d (#43)", [self.CODE]),
            _commit_record("names-commit", "GH-6 e (#44)", [self.CODE]),
            _commit_record("clean", "GH-7 f (#45)", [self.CODE]),
        ]
        sources = _FakeSources(
            pull_requests={43: pool.PullRequestInfo("GH-4/feature", (), "GH-4 d (#43)")},
            revisions={
                1: pool.FirstRevision(None, pool.FIRST_REVISION_UNRECOVERABLE),
                3: _recovered("the fix is in #42"),
                4: _recovered("continue on GH-4/feature"),
                6: _recovered("broke in abc1234"),
                7: _recovered("plain request"),
            },
            known_commits={"abc1234"},
        )
        ceiling = pool.compute_ceiling(commits, sources.build())
        stage_six = ceiling.stages[5]
        assert stage_six.count == 3
        assert dict(stage_six.detail) == {"names_pr_branch_or_commit": 3}
        assert ceiling.informational["stage_6_first_revision_not_recovered"] == 2
        assert ceiling.informational["stage_6_prompt_issue_outside_repository"] == 0

    def test_stage_six_keeps_a_task_whose_issue_only_matches_ambiguously(self):
        commits = [
            _commit_record("common-word-branch", "GH-1 a (#40)", [self.CODE]),
            _commit_record("other-repository-link", "GH-2 b (#41)", [self.CODE]),
        ]
        sources = _FakeSources(
            pull_requests={40: pool.PullRequestInfo("main", (), "GH-1 a (#40)")},
            revisions={
                1: _recovered("update the main page"),
                2: _recovered("see https://github.com/other/repo/pull/41"),
            },
        )
        ceiling = pool.compute_ceiling(commits, sources.build())
        assert _counts(ceiling)["6"] == 2
        assert ceiling.stages[5].detail["names_pr_branch_or_commit"] == 0

    def test_stage_six_drops_a_task_whose_issue_links_this_repositorys_pull_request(self):
        commits = [_commit_record("links-pr", "GH-1 a (#40)", [self.CODE])]
        sources = _FakeSources(revisions={1: _recovered(f"fixed in https://github.com/{REPOSITORY}/pull/40")})
        ceiling = pool.compute_ceiling(commits, sources.build())
        assert _counts(ceiling)["6"] == 0
        assert ceiling.stages[5].detail["names_pr_branch_or_commit"] == 1

    def test_stage_six_keeps_an_issue_outside_the_repository_without_fetching_it(self):
        commits = [_commit_record("foreign", "thing (#40)", [self.CODE])]
        sources = _FakeSources(pull_requests={40: pool.PullRequestInfo(None, (("other/repo", 5),), "thing (#40)")})
        ceiling = pool.compute_ceiling(commits, sources.build())
        assert _counts(ceiling)["2"] == 1
        assert _counts(ceiling)["6"] == 1
        assert dict(ceiling.stages[5].detail) == {"names_pr_branch_or_commit": 0}
        assert ceiling.informational["stage_6_prompt_issue_outside_repository"] == 1
        assert ceiling.informational["stage_6_first_revision_not_recovered"] == 0
        assert sources.requested_issue_numbers == []

    def test_issue_fetch_requests_each_surviving_issue_number_once(self):
        commits = [
            _commit_record("a", "GH-5 a (#40)", [self.CODE]),
            _commit_record("b", "GH-5 b (#41)", [self.CODE]),
            _commit_record("dropped-at-stage-4", "GH-9 c (#42)", [self.CODE, "pyproject.toml"]),
        ]
        sources = _FakeSources(revisions={5: _recovered()})
        pool.compute_ceiling(commits, sources.build())
        assert sources.requested_issue_numbers == [5]

    def test_stage_six_proxy_line_counts_test_file_changes_without_gating(self):
        commits = [
            _commit_record("with-test", "GH-5 a (#40)", [self.CODE, "claude/.claude/scripts/tests/test_tool.py"]),
            _commit_record("without-test", "GH-5 b (#41)", [self.CODE]),
        ]
        ceiling = pool.compute_ceiling(commits, _FakeSources(revisions={5: _recovered()}).build())
        assert ceiling.informational == {
            "stage_6_first_revision_not_recovered": 0,
            "stage_6_prompt_issue_outside_repository": 0,
            "stage_6_also_changes_a_test_file": 1,
        }
        assert _counts(ceiling)["7"] == 2

    def test_last_stage_separates_new_file_only_from_commentless_tasks_and_counts_unparsable_files(self):
        commits = [
            _commit_record("new-only", "GH-5 a (#40)", [self.CODE]),
            _commit_record("commentless", "GH-5 b (#41)", [self.CODE]),
            _commit_record("unparsable", "GH-5 c (#42)", [self.CODE]),
            _commit_record("commented", "GH-5 d (#43)", [self.CODE]),
        ]
        sources = _FakeSources(
            revisions={5: _recovered()},
            judged={
                "new-only": pool.WhyCommentResult(kept=False, new_files_only=True),
                "commentless": pool.WhyCommentResult(kept=False, new_files_only=False),
                "unparsable": pool.WhyCommentResult(kept=True, new_files_only=False, unparsable_files=1),
            },
        )
        ceiling = pool.compute_ceiling(commits, sources.build())
        assert dict(ceiling.stages[-1].detail) == {
            "non_test_changes_all_new_files": 1,
            "no_touched_region_holds_comment": 1,
            "unparsable_base_files_unjudged": 1,
        }
        assert ceiling.surviving_pull_requests == (42, 43)

    def test_empty_history_gives_zero_at_every_stage(self):
        ceiling = pool.compute_ceiling([], _FakeSources().build())
        assert {stage.count for stage in ceiling.stages} == {0}


class TestReport:
    def test_count_below_the_threshold_rules_the_study_out(self):
        assert pool.comparison_line(34) == "CEILING 34 is below the 34.1 threshold: no run count can reach it"

    def test_count_above_the_threshold_does_not_rule_the_study_out(self):
        assert pool.comparison_line(35) == (
            "CEILING 35 is at or above the 34.1 threshold: "
            "the ceiling alone does not rule the study out; the lock's count is the one that binds"
        )

    def test_json_report_is_labeled_and_carries_the_comparison(self):
        ceiling = pool.PoolCeiling(
            stages=(pool.StageResult("7", "last", 40),),
            informational={"stage_6_also_changes_a_test_file": 3},
            surviving_pull_requests=(1, 2),
        )
        report = json.loads(pool.report_json(ceiling, {"ref": "origin/main"}))
        assert report["label"] == "CEILING"
        assert (report["ceiling"], report["threshold"], report["ceiling_meets_threshold"]) == (40, 34.1, True)
        assert report["informational"] == {"stage_6_also_changes_a_test_file": 3}
        assert report["surviving_pull_requests"] == [1, 2]

    def test_json_report_below_the_threshold_does_not_meet_it(self):
        ceiling = pool.PoolCeiling(
            stages=(pool.StageResult("7", "last", 34),),
            informational={"stage_6_also_changes_a_test_file": 0},
            surviving_pull_requests=(),
        )
        report = json.loads(pool.report_json(ceiling, {"ref": "origin/main"}))
        assert (report["ceiling"], report["ceiling_meets_threshold"]) == (34, False)

    def test_human_table_lists_each_stage_with_its_detail_every_info_entry_and_ends_on_the_comparison(self):
        ceiling = pool.PoolCeiling(
            stages=(
                pool.StageResult("6", "first revision recovered", 50, {"names_pr_branch_or_commit": 2}),
                pool.StageResult("7", "last", 30),
            ),
            informational={"stage_6_first_revision_not_recovered": 4, "stage_6_also_changes_a_test_file": 3},
            surviving_pull_requests=(),
        )
        lines = pool.format_report(ceiling, {"ref": "origin/main"}).splitlines()
        assert lines == [
            "CEILING on the PR-task count: reads no test result and no outcome. Every figure is an upper bound.",
            "Skipped conditions: the fail-to-pass condition, the specification filter, the stripping checks, "
            "the loaded-configuration check.",
            "ref: origin/main",
            "",
            "stage  count  name",
            "6         50  first revision recovered",
            "                 names_pr_branch_or_commit: 2",
            "7         30  last",
            "info       4  stage_6_first_revision_not_recovered (not a gate)",
            "info       3  stage_6_also_changes_a_test_file (not a gate)",
            "",
            "CEILING 30 is below the 34.1 threshold: no run count can reach it",
        ]


class TestGitReading:
    @staticmethod
    def _repo_with_history(tmp_path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "tool.py", "def f():\n    # why\n    return 1\n\n\ndef g():\n    return 2\n")
        _commit(repo, "initial")
        base = _git(repo, "rev-parse", "HEAD").strip()
        _write(repo, "tool.py", "def f():\n    # why\n    return 10\n\n\ndef g():\n    return 2\n")
        _write(repo, "new_helper.sh", "echo hi\n")
        change = _commit(repo, "GH-5 change f (#40)")
        _write(repo, "docs/case-studies/comments-stripped-ab.md", "page\n")
        study = _commit(repo, "add the study page (#41)")
        return repo, base, change, study

    def test_reads_first_parent_commits_with_their_changes(self, tmp_path):
        repo, base, change, _ = self._repo_with_history(tmp_path)
        commits = {commit.sha: commit for commit in pool.read_first_parent_commits(repo, "HEAD")}
        assert commits[change].parents == (base,)
        assert commits[change].subject == "GH-5 change f (#40)"
        assert set(commits[change].changes) == {("M", "tool.py"), ("A", "new_helper.sh")}
        assert commits[base].parents == ()

    def test_finds_the_first_commit_adding_a_study_artifact(self, tmp_path):
        repo, _, _, study = self._repo_with_history(tmp_path)
        assert pool.find_study_start_commit(repo, "HEAD") == study

    @pytest.mark.parametrize(
        ("earlier_artifact", "later_artifact"),
        [
            (".claude/plans/comment-ab-experiment-design.md", "docs/case-studies/comments-stripped-ab.md"),
            ("docs/case-studies/comments-stripped-ab.md", ".claude/plans/comment-ab-experiment-design.md"),
        ],
        ids=["plan-file-first", "design-page-first"],
    )
    def test_earliest_of_two_study_artifact_commits_wins(self, tmp_path, earlier_artifact, later_artifact):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "tool.py", "def f():\n    return 1\n")
        _commit(repo, "initial")
        _write(repo, earlier_artifact, "first artifact\n")
        earlier = _commit(repo, "add the first artifact (#41)")
        _write(repo, later_artifact, "second artifact\n")
        later = _commit(repo, "add the second artifact (#42)")
        assert pool.find_study_start_commit(repo, "HEAD") == earlier != later

    @staticmethod
    def _repo_with_side_branch_merge(tmp_path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "tool.py", "def f():\n    return 1\n")
        root = _commit(repo, "initial")
        _git(repo, "checkout", "-q", "-b", "side")
        _write(repo, "side.py", "def s():\n    return 1\n")
        _write(repo, "docs/case-studies/comments-stripped-ab.md", "page\n")
        side = _commit(repo, "side work (#50)")
        _git(repo, "checkout", "-q", "main")
        _write(repo, "tool.py", "def f():\n    return 2\n")
        mainline = _commit(repo, "mainline work (#51)")
        _git(repo, "merge", "-q", "--no-ff", "side", "-m", "merge the side branch (#52)")
        merge = _git(repo, "rev-parse", "HEAD").strip()
        return repo, root, side, mainline, merge

    def test_first_parent_history_skips_side_branch_commits_and_keeps_both_merge_parents(self, tmp_path):
        repo, root, side, mainline, merge = self._repo_with_side_branch_merge(tmp_path)
        commits = pool.read_first_parent_commits(repo, "HEAD")
        assert [commit.sha for commit in commits] == [merge, mainline, root]
        assert commits[0].parents == (mainline, side)
        assert set(commits[0].changes) == {("A", "side.py"), ("A", "docs/case-studies/comments-stripped-ab.md")}

    def test_study_start_is_the_first_parent_commit_that_brings_an_artifact_in(self, tmp_path):
        repo, _, _, _, merge = self._repo_with_side_branch_merge(tmp_path)
        assert pool.find_study_start_commit(repo, "HEAD") == merge

    @pytest.mark.parametrize("read", [pool.read_first_parent_commits, pool.find_study_start_commit])
    def test_ref_starting_with_a_dash_is_rejected_by_git_not_read_as_an_option(self, tmp_path, read):
        repo, *_ = self._repo_with_history(tmp_path)
        leaked_log = tmp_path / "leaked-log.txt"
        with pytest.raises(pool.GitError):
            read(repo, f"--output={leaked_log}")
        assert not leaked_log.exists()

    @staticmethod
    def _repo_with_nested_and_spaced_paths(tmp_path):
        def python_source(commented_value, bare_value):
            return f"def f():\n    # why\n    return {commented_value}\n\n\ndef g():\n    return {bare_value}\n"

        def shell_source(commented_value, bare_value):
            return f"f() {{\n  # why\n  echo {commented_value}\n}}\n\ng() {{\n  echo {bare_value}\n}}\n"

        spaced_path = "src/pkg dir/my module.py"
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "src/pkg/mod.py", python_source(1, 1))
        _write(repo, "scripts/run.sh", shell_source(1, 1))
        _write(repo, spaced_path, python_source(1, 1))
        _commit(repo, "initial")
        shas = {}
        _write(repo, "src/pkg/mod.py", python_source(2, 1))
        shas["commented-function-in-nested-python"] = _commit(repo, "GH-1 nested python (#60)")
        _write(repo, "scripts/run.sh", shell_source(2, 1))
        shas["commented-function-in-nested-shell"] = _commit(repo, "GH-1 nested shell (#61)")
        _write(repo, spaced_path, python_source(2, 1))
        shas["commented-function-in-spaced-path"] = _commit(repo, "GH-1 spaced path (#62)")
        _write(repo, "src/pkg/mod.py", python_source(2, 3))
        shas["bare-function-in-nested-python"] = _commit(repo, "GH-1 bare function (#63)")
        return repo, shas, spaced_path

    @pytest.mark.parametrize(
        ("label", "expected_kept"),
        [
            ("commented-function-in-nested-python", True),
            ("commented-function-in-nested-shell", True),
            ("commented-function-in-spaced-path", True),
            ("bare-function-in-nested-python", False),
        ],
    )
    def test_judges_a_change_under_a_nested_or_spaced_path(self, tmp_path, label, expected_kept):
        repo, shas, _ = self._repo_with_nested_and_spaced_paths(tmp_path)
        commit = next(commit for commit in pool.read_first_parent_commits(repo, "HEAD") if commit.sha == shas[label])
        result = pool.git_why_comment_judge(repo)(commit)
        assert result == pool.WhyCommentResult(kept=expected_kept, new_files_only=False)

    def test_reads_a_path_containing_a_space_as_one_path(self, tmp_path):
        repo, shas, spaced_path = self._repo_with_nested_and_spaced_paths(tmp_path)
        commits = {commit.sha: commit for commit in pool.read_first_parent_commits(repo, "HEAD")}
        assert commits[shas["commented-function-in-spaced-path"]].changes == (("M", spaced_path),)

    def test_reports_no_study_start_when_the_ref_predates_the_artifact(self, tmp_path):
        repo, _, change, _ = self._repo_with_history(tmp_path)
        assert pool.find_study_start_commit(repo, change) is None

    def test_ancestors_before_the_study_commit_exclude_it(self, tmp_path):
        repo, base, change, study = self._repo_with_history(tmp_path)
        assert pool.read_ancestors_before(repo, study) == {base, change}

    def test_judges_a_real_diff_against_the_base_blob(self, tmp_path):
        repo, _, change, _ = self._repo_with_history(tmp_path)
        commit = next(commit for commit in pool.read_first_parent_commits(repo, "HEAD") if commit.sha == change)
        assert pool.git_why_comment_judge(repo)(commit) == pool.WhyCommentResult(kept=True, new_files_only=False)

    def test_keeps_and_counts_a_change_to_a_base_blob_that_does_not_parse(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "broken.py", "def broken(:\n")
        _commit(repo, "initial")
        _write(repo, "broken.py", "def broken():\n")
        change = _commit(repo, "GH-5 repair the syntax (#40)")
        commit = next(commit for commit in pool.read_first_parent_commits(repo, "HEAD") if commit.sha == change)
        result = pool.git_why_comment_judge(repo)(commit)
        assert result == pool.WhyCommentResult(kept=True, new_files_only=False, unparsable_files=1)

    def test_keeps_and_counts_a_change_to_a_non_ascii_path_that_git_quotes(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "caf\u00e9.py", "def f():\n    return 1\n")
        _commit(repo, "initial")
        _write(repo, "caf\u00e9.py", "def f():\n    return 2\n")
        change = _commit(repo, "GH-5 change f (#40)")
        result = pool.git_why_comment_judge(repo)(self._change_commit(repo, change))
        assert result == pool.WhyCommentResult(kept=True, new_files_only=False, unparsable_files=1)

    @pytest.mark.parametrize(
        ("script_name", "shebang"),
        [("run-tool", "#!/usr/bin/env python3"), ("run-tool.pyi", "#!/bin/sh")],
        ids=["extensionless-python-script", "other-suffix-with-a-shell-shebang"],
    )
    def test_keeps_and_counts_a_modified_script_the_suffix_filter_skips(self, tmp_path, script_name, shebang):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "tool.py", "def f():\n    return 1\n")
        _write(repo, script_name, f"{shebang}\n# why\nrun_it\n")
        _commit(repo, "initial")
        _write(repo, "tool.py", "def f():\n    return 2\n")
        _write(repo, script_name, f"{shebang}\n# why\nrun_it_again\n")
        change = _commit(repo, "GH-5 change both (#40)")
        result = pool.git_why_comment_judge(repo)(self._change_commit(repo, change))
        assert result == pool.WhyCommentResult(kept=True, new_files_only=False, unparsable_files=1)

    def test_drops_a_change_beside_a_modified_text_file_that_has_no_shebang(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "tool.py", "def f():\n    return 1\n")
        _write(repo, "notes", "# a heading\n")
        _commit(repo, "initial")
        _write(repo, "tool.py", "def f():\n    return 2\n")
        _write(repo, "notes", "# another heading\n")
        change = _commit(repo, "GH-5 change both (#40)")
        result = pool.git_why_comment_judge(repo)(self._change_commit(repo, change))
        assert result == pool.WhyCommentResult(kept=False, new_files_only=False)

    @pytest.mark.parametrize("hostile_path", ["a\nb.md", "notes\r"], ids=["newline", "trailing-carriage-return"])
    def test_keeps_and_counts_a_modified_path_with_a_line_break_without_shifting_the_next_script(
        self, tmp_path, monkeypatch, hostile_path
    ):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "tool.py", "def f():\n    return 1\n")
        _write(repo, hostile_path, "plain text\n")
        _write(repo, "run-tool", "#!/bin/sh\nrun_it\n")
        _commit(repo, "initial")
        _write(repo, "tool.py", "def f():\n    return 2\n")
        _write(repo, hostile_path, "other text\n")
        _write(repo, "run-tool", "#!/bin/sh\nrun_it_again\n")
        change = _commit(repo, "GH-5 change all three (#40)")
        sent_specs = []
        read_blobs = pool.read_blobs

        def record_specs(repo, specs):
            sent_specs.extend(specs)
            return read_blobs(repo, specs)

        monkeypatch.setattr(pool, "read_blobs", record_specs)
        result = pool.git_why_comment_judge(repo)(self._change_commit(repo, change))
        assert result == pool.WhyCommentResult(kept=True, new_files_only=False, unparsable_files=2)
        assert sent_specs
        assert not any("\n" in spec or "\r" in spec for spec in sent_specs)

    @pytest.mark.parametrize(
        "response",
        [b"abcd ambiguous\n", b"HEAD:other.py missing\n", b"", (b"0" * 40 + b" blob 3\nabc\n") * 2],
        ids=["ambiguous", "missing-notice-for-another-spec", "no-response", "more-responses-than-specs"],
    )
    def test_read_blobs_fails_closed_on_a_response_that_does_not_match_the_specs(self, tmp_path, monkeypatch, response):
        monkeypatch.setattr(pool, "_run_git", lambda *args, **kwargs: response)
        with pytest.raises(pool.GitError):
            pool.read_blobs(tmp_path, ["HEAD:tool.py"])

    def test_read_blobs_fails_closed_on_a_blob_shorter_than_its_header_says(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pool, "_run_git", lambda *args, **kwargs: b"0" * 40 + b" blob 9\nabc\n")
        with pytest.raises(pool.GitError):
            pool.read_blobs(tmp_path, ["HEAD:tool.py"])

    def test_is_shallow_repository_tells_a_shallow_clone_from_a_full_one(self, tmp_path):
        repo, *_ = self._repo_with_history(tmp_path)
        clone = tmp_path / "clone"
        _git(tmp_path, "clone", "-q", "--depth", "1", f"file://{repo}", str(clone))
        assert not pool.is_shallow_repository(repo)
        assert pool.is_shallow_repository(clone)

    def test_read_blobs_marks_a_missing_path_none(self, tmp_path):
        repo, base, _, _ = self._repo_with_history(tmp_path)
        blobs = pool.read_blobs(repo, [f"{base}:tool.py", f"{base}:absent.py"])
        assert blobs[f"{base}:tool.py"].startswith("def f()")
        assert blobs[f"{base}:absent.py"] is None

    def test_read_blobs_keeps_a_multibyte_blob_from_shifting_the_next_one(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "accented.py", "# café\n")
        _write(repo, "plain.py", "x = 1\n")
        _commit(repo, "initial")
        blobs = pool.read_blobs(repo, ["HEAD:accented.py", "HEAD:plain.py"])
        assert blobs == {"HEAD:accented.py": "# café\n", "HEAD:plain.py": "x = 1\n"}

    def test_git_names_commit_resolves_an_abbreviation_and_rejects_an_unknown_one(self, tmp_path):
        repo, base, _, _ = self._repo_with_history(tmp_path)
        is_commit = pool.git_names_commit(repo)
        assert is_commit(base[:10])
        assert not is_commit("abc1234")

    @staticmethod
    def _change_commit(repo, sha):
        return next(commit for commit in pool.read_first_parent_commits(repo, "HEAD") if commit.sha == sha)

    @pytest.mark.parametrize(
        ("changed_function", "expected_kept"), [("commented", True), ("bare", False)], ids=["commented-function", "bare-function"]
    )
    def test_a_repository_subdirectory_gives_the_root_study_start_and_verdicts(self, tmp_path, changed_function, expected_kept):
        # A pathspec resolves against the working directory, so one that is not anchored to the root matches only below it.
        def python_source(commented_value, bare_value):
            return f"def f():\n    # why\n    return {commented_value}\n\n\ndef g():\n    return {bare_value}\n"

        repo = _init_repo(tmp_path / "repo")
        _write(repo, "scripts/keep.sh", "echo hi\n")
        _write(repo, "other/mod.py", python_source(1, 1))
        _commit(repo, "initial")
        _write(repo, "other/mod.py", python_source(2, 1) if changed_function == "commented" else python_source(1, 3))
        change = _commit(repo, "GH-5 change a function (#40)")
        _write(repo, "docs/case-studies/comments-stripped-ab.md", "page\n")
        study = _commit(repo, "add the study page (#41)")
        subdirectory = repo / "scripts"
        commit = self._change_commit(repo, change)
        expected = pool.WhyCommentResult(kept=expected_kept, new_files_only=False)
        assert pool.find_study_start_commit(subdirectory, "HEAD") == pool.find_study_start_commit(repo, "HEAD") == study
        assert pool.git_why_comment_judge(repo)(commit) == expected
        assert pool.git_why_comment_judge(subdirectory)(commit) == expected

    @staticmethod
    def _second_repository(tmp_path):
        other = _init_repo(tmp_path / "other")
        _write(other, "unrelated.py", "x = 1\n")
        _commit(other, "unrelated history")
        return other

    @pytest.mark.parametrize(
        "read",
        [
            lambda repo, commit: pool.read_first_parent_commits(repo, "HEAD"),
            lambda repo, commit: pool.find_study_start_commit(repo, "HEAD"),
            lambda repo, commit: pool.read_ancestors_before(repo, commit.sha),
            lambda repo, commit: pool.git_names_commit(repo)(commit.sha[:10]),
            lambda repo, commit: pool.read_blobs(repo, [f"{commit.sha}:tool.py"]),
            lambda repo, commit: pool.git_why_comment_judge(repo)(commit),
        ],
        ids=["first-parent-commits", "study-start", "ancestors", "names-commit", "blobs", "why-comment-judge"],
    )
    def test_an_inherited_git_dir_does_not_redirect_a_read_to_another_repository(self, tmp_path, monkeypatch, read):
        repo, _, change, _ = self._repo_with_history(tmp_path)
        other = self._second_repository(tmp_path)
        commit = self._change_commit(repo, change)
        expected = read(repo, commit)
        monkeypatch.setenv("GIT_DIR", str(other / ".git"))
        assert expected
        assert read(repo, commit) == expected

    @pytest.mark.parametrize("configured_through", ["repository-config", "environment"])
    def test_an_external_diff_program_never_runs_while_judging(self, tmp_path, monkeypatch, configured_through):
        repo, _, change, _ = self._repo_with_history(tmp_path)
        marker = tmp_path / "external-diff-ran"
        stub = tmp_path / "external-diff.sh"
        stub.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
        stub.chmod(0o755)
        if configured_through == "repository-config":
            _git(repo, "config", "diff.external", str(stub))
        else:
            monkeypatch.setenv("GIT_EXTERNAL_DIFF", str(stub))
        result = pool.git_why_comment_judge(repo)(self._change_commit(repo, change))
        assert result == pool.WhyCommentResult(kept=True, new_files_only=False)
        assert not marker.exists()

    def test_a_diff_attribute_that_hides_text_changes_does_not_hide_the_hunks(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, ".gitattributes", "*.py -diff\n")
        _write(repo, "tool.py", "def f():\n    return 1\n")
        _commit(repo, "initial")
        _write(repo, "tool.py", "def f():\n    return 2\n")
        change = _commit(repo, "GH-5 change f (#40)")
        result = pool.git_why_comment_judge(repo)(self._change_commit(repo, change))
        assert result == pool.WhyCommentResult(kept=False, new_files_only=False)

    def test_keeps_and_counts_a_mode_only_change_that_has_no_hunks(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "tool.py", "def f():\n    return 1\n")
        _commit(repo, "initial")
        (repo / "tool.py").chmod(0o755)
        change = _commit(repo, "GH-5 make it executable (#40)")
        result = pool.git_why_comment_judge(repo)(self._change_commit(repo, change))
        assert result == pool.WhyCommentResult(kept=True, new_files_only=False, unparsable_files=1)

    @staticmethod
    def _recorded_diff_argv(monkeypatch):
        seen_argv = []

        def record_git(repo, args, **kwargs):
            seen_argv.append(list(args))
            return b""

        monkeypatch.setattr(pool, "_run_git", record_git)
        commit = _commit_record("c" * 40, "GH-5 thing (#40)", ["tool.py"], parents=("b" * 40,))
        pool.git_why_comment_judge(Path("."))(commit)
        (argv,) = seen_argv
        return argv

    def test_diff_revisions_follow_an_end_of_options_marker_and_pathspecs_start_at_the_root(self, monkeypatch):
        argv = self._recorded_diff_argv(monkeypatch)
        assert argv.index("--end-of-options") < argv.index("b" * 40) < argv.index("c" * 40) < argv.index("--")
        assert "--text" in argv
        assert argv[argv.index("--") + 1 :] == [":(top)*.py", ":(top)*.sh"]

    def test_diff_argv_carries_every_shared_diff_text_argument(self, monkeypatch):
        argv = self._recorded_diff_argv(monkeypatch)
        assert pool.DIFF_TEXT_ARGS
        assert set(pool.DIFF_TEXT_ARGS) <= set(argv)

    SHA = "a" * 40
    OTHER_SHA = "b" * 40

    @staticmethod
    def _log_output(*records):
        return "".join(f"\x01{sha}\0{parents}\0{subject}\0\nM\0tool.py\0" for sha, parents, subject in records).encode()

    def test_accepts_sha1_and_sha256_object_ids(self, monkeypatch):
        sha256 = "c" * 64
        output = self._log_output((self.SHA, f"{self.OTHER_SHA} {sha256}", "merge (#1)"), (sha256, "", "root"))
        monkeypatch.setattr(pool, "_run_git", lambda repo, args, **kwargs: output)
        commits = pool.read_first_parent_commits(Path("."), "HEAD")
        assert [(commit.sha, commit.parents) for commit in commits] == [
            (self.SHA, (self.OTHER_SHA, sha256)),
            (sha256, ()),
        ]

    @pytest.mark.parametrize(
        "record",
        [
            ("not-a-sha", "", "subject"),
            ("--output=leaked", "", "subject"),
            ("A" * 40, "", "subject"),
            ("a" * 39, "", "subject"),
            (SHA, "--output=leaked", "subject"),
            (SHA, f"{OTHER_SHA} zzz", "subject"),
        ],
        ids=["not-hex", "option-shaped", "uppercase", "short", "option-shaped-parent", "bad-second-parent"],
    )
    def test_rejects_a_log_record_whose_sha_or_parent_is_not_an_object_id(self, monkeypatch, record):
        monkeypatch.setattr(pool, "_run_git", lambda repo, args, **kwargs: self._log_output(record))
        with pytest.raises(pool.GitError, match="where a commit id belongs"):
            pool.read_first_parent_commits(Path("."), "HEAD")

    def test_rejects_a_log_record_cut_short_of_its_fields(self, monkeypatch):
        monkeypatch.setattr(pool, "_run_git", lambda repo, args, **kwargs: f"\x01{self.SHA}\0".encode())
        with pytest.raises(pool.GitError, match="fewer than 3"):
            pool.read_first_parent_commits(Path("."), "HEAD")


# --- GitHub layer ------------------------------------------------------------------------------


def _pull_request_node(head_ref="branch", closing=(), closing_total=None, title="a pull request title"):
    nodes = [{"number": number, "repository": {"nameWithOwner": repository}} for repository, number in closing]
    return {
        "headRefName": head_ref,
        "title": title,
        "closingIssuesReferences": {"totalCount": len(nodes) if closing_total is None else closing_total, "nodes": nodes},
    }


def _issue_node(pages, *, last_edited_at="2026-02-01T00:00:00Z"):
    """An issue as `_FakeGraphql` serves it. `pages` holds one list of edit nodes per `userContentEdits` page."""
    return {"createdAt": "2026-01-02T03:04:05Z", "body": "current body", "lastEditedAt": last_edited_at, "pages": pages}


class _FakeGraphql:
    """Stands in for `pool._graphql`. It reads the aliased numbers and the issue number out of the query text, so
    it answers only the three query shapes the fetchers build.

    An absent number comes back null beside a NOT_FOUND error. `alias_errors` replaces that error for an alias,
    and a None value there leaves the alias null with no error."""

    def __init__(self, pull_requests=None, issues=None, alias_errors=None, extra_errors=(), page_errors=()):
        self.pull_requests = pull_requests or {}
        self.issues = issues or {}
        self.alias_errors = alias_errors or {}
        self.extra_errors = list(extra_errors)
        self.page_errors = list(page_errors)
        self.calls = []
        self.variables = []
        self.queries = []

    def __call__(self, repo, query, variables):
        self.variables.append(dict(variables))
        self.queries.append(query)
        if "$cursor" in query:
            return self._edit_page_payload(query, variables["cursor"])
        aliases = re.findall(r"\b([pi])(\d+): (?:pullRequest|issue)\(number", query)
        self.calls.append(("batch", [int(number) for _, number in aliases]))
        repository = {}
        errors = list(self.extra_errors)
        for kind, number in aliases:
            alias = f"{kind}{number}"
            node = (self.pull_requests if kind == "p" else self.issues).get(int(number))
            if node is None:
                repository[alias] = None
                error = self.alias_errors.get(
                    alias, {"type": "NOT_FOUND", "path": ["repository", alias], "message": "Could not resolve"}
                )
                if error is not None:
                    errors.append(error)
            elif kind == "p":
                repository[alias] = node
            else:
                repository[alias] = {
                    **{key: value for key, value in node.items() if key != "pages"},
                    "userContentEdits": self._edit_page(node["pages"], 0),
                }
        payload = {"data": {"repository": repository}}
        if errors:
            payload["errors"] = errors
        return payload

    def _edit_page_payload(self, query, cursor):
        number = int(re.search(r"issue\(number: (\d+)\)", query).group(1))
        self.calls.append(("page", number, cursor))
        issue = {"userContentEdits": self._edit_page(self.issues[number]["pages"], int(cursor))}
        payload = {"data": {"repository": {"issue": issue}}}
        if self.page_errors:
            payload["errors"] = self.page_errors
        return payload

    @staticmethod
    def _edit_page(pages, index):
        return {"nodes": pages[index], "pageInfo": {"hasNextPage": index + 1 < len(pages), "endCursor": str(index + 1)}}


def _assert_every_query_targets(fake, repository):
    owner, name = repository.split("/")
    assert fake.variables
    assert all((variables["owner"], variables["name"]) == (owner, name) for variables in fake.variables)


def _cache(tmp_path):
    return pool.GitHubCache(tmp_path / "cache" / "owner__repo.json", refresh=False)


ABSENCE_ERRORS = {
    "forbidden": lambda alias: {"type": "FORBIDDEN", "path": ["repository", alias], "message": "Resource not accessible"},
    "rate-limited": lambda alias: {"type": "RATE_LIMITED", "path": ["repository", alias], "message": "API rate limit"},
    "not-found-naming-the-repository": lambda alias: {"type": "NOT_FOUND", "path": ["repository"], "message": "No repository"},
    "not-found-naming-an-alias-outside-the-batch": lambda alias: {
        "type": "NOT_FOUND",
        "path": ["repository", "x999"],
        "message": "Could not resolve",
    },
    "null-with-no-error": lambda alias: None,
}


class TestGitHubCache:
    ROW = {"text": "body", "reason": pool.FIRST_REVISION_UNEDITED}

    def test_saved_entries_round_trip_through_a_new_cache(self, tmp_path):
        path = tmp_path / "nested" / "cache.json"
        cache = pool.GitHubCache(path, refresh=False)
        cache.put("issues", 5, self.ROW)
        cache.save()
        assert pool.GitHubCache(path, refresh=False).get("issues", 5) == self.ROW

    def test_refresh_ignores_the_saved_file(self, tmp_path):
        path = tmp_path / "cache.json"
        cache = pool.GitHubCache(path, refresh=False)
        cache.put("issues", 5, self.ROW)
        cache.save()
        assert pool.GitHubCache(path, refresh=True).get("issues", 5) is None

    def test_entry_that_was_never_saved_is_absent_from_a_new_cache(self, tmp_path):
        path = tmp_path / "cache.json"
        pool.GitHubCache(path, refresh=False).put("issues", 5, self.ROW)
        assert pool.GitHubCache(path, refresh=False).get("issues", 5) is None


class TestPullRequestFetcher:
    @staticmethod
    def _fetcher(tmp_path, monkeypatch, fake):
        monkeypatch.setattr(pool, "_graphql", fake)
        cache = _cache(tmp_path)
        return pool.github_pull_request_fetcher(tmp_path, REPOSITORY, cache), cache

    @pytest.mark.parametrize(("count", "expected_batches"), [(25, 1), (26, 2), (50, 2), (51, 3)])
    def test_returns_one_entry_per_number_across_the_batch_boundary(self, tmp_path, monkeypatch, count, expected_batches):
        numbers = list(range(1, count + 1))
        fake = _FakeGraphql(
            pull_requests={number: _pull_request_node(f"branch-{number}", title=f"title-{number}") for number in numbers}
        )
        fetch, _ = self._fetcher(tmp_path, monkeypatch, fake)
        result = fetch(numbers)
        batches = [call[1] for call in fake.calls]
        assert sorted(result) == numbers
        assert result[count] == pool.PullRequestInfo(f"branch-{count}", (), f"title-{count}")
        assert len(batches) == expected_batches
        assert sorted(number for batch in batches for number in batch) == numbers
        assert max(len(batch) for batch in batches) <= pool.GRAPHQL_BATCH_SIZE

    def test_queries_target_the_given_repository(self, tmp_path, monkeypatch):
        fake = _FakeGraphql(pull_requests={40: _pull_request_node()})
        fetch, _ = self._fetcher(tmp_path, monkeypatch, fake)
        fetch([40])
        _assert_every_query_targets(fake, REPOSITORY)

    def test_a_null_repository_without_errors_raises_and_caches_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pool, "_graphql", lambda repo, query, variables: {"data": {"repository": None}})
        cache = _cache(tmp_path)
        fetch = pool.github_pull_request_fetcher(tmp_path, REPOSITORY, cache)
        with pytest.raises(pool.GitHubError, match="no repository"):
            fetch([40])
        assert cache.get("pull_requests", 40) is None
        assert not (tmp_path / "cache").exists()

    def test_closing_references_past_the_listed_nodes_stay_as_placeholders(self, tmp_path, monkeypatch):
        fake = _FakeGraphql(pull_requests={40: _pull_request_node(closing=[(REPOSITORY, 5)], closing_total=3)})
        fetch, _ = self._fetcher(tmp_path, monkeypatch, fake)
        info = fetch([40])[40]
        assert info.closing_issues == ((REPOSITORY, 5), ("", 0), ("", 0))
        assert pool.prompt_issue_for("thing (#40)", 40, info.closing_issues, REPOSITORY) is None

    def test_number_the_repository_does_not_hold_is_cached_as_having_no_closing_issues(self, tmp_path, monkeypatch):
        fake = _FakeGraphql(pull_requests={6: _pull_request_node()})
        fetch, _ = self._fetcher(tmp_path, monkeypatch, fake)
        assert fetch([5, 6])[5] == pool.PullRequestInfo(None, (), None)
        fetch([5, 6])
        assert len(fake.calls) == 1

    @pytest.mark.parametrize("make_error", ABSENCE_ERRORS.values(), ids=ABSENCE_ERRORS.keys())
    def test_any_other_absence_raises_and_caches_nothing_from_the_batch(self, tmp_path, monkeypatch, make_error):
        fake = _FakeGraphql(pull_requests={6: _pull_request_node()}, alias_errors={"p5": make_error("p5")})
        fetch, cache = self._fetcher(tmp_path, monkeypatch, fake)
        with pytest.raises(pool.GitHubError):
            fetch([5, 6])
        assert (cache.get("pull_requests", 5), cache.get("pull_requests", 6)) == (None, None)
        assert not (tmp_path / "cache").exists()

    def test_error_beside_a_resolved_alias_raises_and_caches_nothing(self, tmp_path, monkeypatch):
        forbidden = {"type": "FORBIDDEN", "path": ["repository", "p6", "closingIssuesReferences"], "message": "no access"}
        fake = _FakeGraphql(pull_requests={6: _pull_request_node()}, extra_errors=[forbidden])
        fetch, cache = self._fetcher(tmp_path, monkeypatch, fake)
        with pytest.raises(pool.GitHubError):
            fetch([6])
        assert cache.get("pull_requests", 6) is None

    def test_populated_cache_serves_a_second_fetch_without_a_call(self, tmp_path, monkeypatch):
        fake = _FakeGraphql(pull_requests={40: _pull_request_node("branch-40")})
        fetch, _ = self._fetcher(tmp_path, monkeypatch, fake)
        first = fetch([40])
        assert fetch([40]) == first
        assert len(fake.calls) == 1

    def test_title_flows_through_from_the_query_result(self, tmp_path, monkeypatch):
        fake = _FakeGraphql(pull_requests={40: _pull_request_node(title="GH-5 rework the thing")})
        fetch, _ = self._fetcher(tmp_path, monkeypatch, fake)
        assert fetch([40])[40].title == "GH-5 rework the thing"
        assert re.search(r"\btitle\b", fake.queries[0])

    def test_cache_row_saved_without_a_title_is_fetched_again(self, tmp_path, monkeypatch):
        fake = _FakeGraphql(pull_requests={40: _pull_request_node("branch-40", title="fresh title")})
        fetch, cache = self._fetcher(tmp_path, monkeypatch, fake)
        cache.put("pull_requests", 40, {"head_ref": "branch-40", "closing": []})
        assert fetch([40])[40].title == "fresh title"
        assert len(fake.calls) == 1

    def test_cache_saved_by_one_run_serves_the_next_run(self, tmp_path, monkeypatch):
        fake = _FakeGraphql(pull_requests={40: _pull_request_node("branch-40")})
        first_fetch, _ = self._fetcher(tmp_path, monkeypatch, fake)
        first_fetch([40])
        second_fetch, _ = self._fetcher(tmp_path, monkeypatch, fake)
        assert second_fetch([40])[40] == pool.PullRequestInfo("branch-40", (), "a pull request title")
        assert len(fake.calls) == 1


class TestFirstRevisionFetcher:
    ORIGINAL_EDIT = {"editedAt": "2026-01-02T03:04:05Z", "diff": "original"}
    MIDDLE_EDIT = {"editedAt": "2026-01-15T00:00:00Z", "diff": "middle"}
    LATER_EDIT = {"editedAt": "2026-02-01T00:00:00Z", "diff": "newest"}

    @staticmethod
    def _fetcher(tmp_path, monkeypatch, fake):
        monkeypatch.setattr(pool, "_graphql", fake)
        cache = _cache(tmp_path)
        return pool.github_first_revision_fetcher(tmp_path, REPOSITORY, cache), cache

    @pytest.mark.parametrize(("count", "expected_batches"), [(26, 2), (50, 2), (51, 3)])
    def test_returns_one_entry_per_number_across_the_batch_boundary(self, tmp_path, monkeypatch, count, expected_batches):
        numbers = list(range(1, count + 1))
        fake = _FakeGraphql(issues={number: _issue_node([[]], last_edited_at=None) for number in numbers})
        fetch, _ = self._fetcher(tmp_path, monkeypatch, fake)
        result = fetch(numbers)
        assert sorted(result) == numbers
        assert result[count] == pool.FirstRevision("current body", pool.FIRST_REVISION_UNEDITED)
        assert len(fake.calls) == expected_batches

    def test_oldest_edit_that_is_only_on_the_second_page_is_found(self, tmp_path, monkeypatch):
        fake = _FakeGraphql(issues={5: _issue_node([[self.LATER_EDIT], [self.ORIGINAL_EDIT]])})
        fetch, _ = self._fetcher(tmp_path, monkeypatch, fake)
        assert fetch([5])[5] == pool.FirstRevision("original", pool.FIRST_REVISION_OLDEST_EDIT)
        assert fake.calls == [("batch", [5]), ("page", 5, "1")]

    def test_oldest_edit_on_the_third_page_is_found_by_advancing_the_cursor_each_page(self, tmp_path, monkeypatch):
        fake = _FakeGraphql(issues={5: _issue_node([[self.LATER_EDIT], [self.MIDDLE_EDIT], [self.ORIGINAL_EDIT]])})
        fetch, _ = self._fetcher(tmp_path, monkeypatch, fake)
        assert fetch([5])[5] == pool.FirstRevision("original", pool.FIRST_REVISION_OLDEST_EDIT)
        assert fake.calls == [("batch", [5]), ("page", 5, "1"), ("page", 5, "2")]

    def test_batch_and_edit_page_queries_target_the_given_repository(self, tmp_path, monkeypatch):
        fake = _FakeGraphql(issues={5: _issue_node([[self.LATER_EDIT], [self.ORIGINAL_EDIT]])})
        fetch, _ = self._fetcher(tmp_path, monkeypatch, fake)
        fetch([5])
        assert [call[0] for call in fake.calls] == ["batch", "page"]
        _assert_every_query_targets(fake, REPOSITORY)

    def test_number_the_repository_does_not_hold_is_cached_as_missing(self, tmp_path, monkeypatch):
        fake = _FakeGraphql(issues={6: _issue_node([[]], last_edited_at=None)})
        fetch, _ = self._fetcher(tmp_path, monkeypatch, fake)
        assert fetch([5, 6])[5] == pool.FirstRevision(None, pool.FIRST_REVISION_MISSING)
        fetch([5, 6])
        assert len(fake.calls) == 1

    @pytest.mark.parametrize("make_error", ABSENCE_ERRORS.values(), ids=ABSENCE_ERRORS.keys())
    def test_any_other_absence_raises_and_caches_nothing_from_the_batch(self, tmp_path, monkeypatch, make_error):
        fake = _FakeGraphql(
            issues={6: _issue_node([[]], last_edited_at=None)}, alias_errors={"i5": make_error("i5")}
        )
        fetch, cache = self._fetcher(tmp_path, monkeypatch, fake)
        with pytest.raises(pool.GitHubError):
            fetch([5, 6])
        assert (cache.get("issues", 5), cache.get("issues", 6)) == (None, None)
        assert not (tmp_path / "cache").exists()

    def test_error_on_a_later_edit_page_raises_and_caches_nothing(self, tmp_path, monkeypatch):
        rate_limited = {"type": "RATE_LIMITED", "path": ["repository", "issue"], "message": "API rate limit"}
        fake = _FakeGraphql(
            issues={5: _issue_node([[self.LATER_EDIT], [self.ORIGINAL_EDIT]])}, page_errors=[rate_limited]
        )
        fetch, cache = self._fetcher(tmp_path, monkeypatch, fake)
        with pytest.raises(pool.GitHubError):
            fetch([5])
        assert cache.get("issues", 5) is None

    def test_populated_cache_serves_a_second_fetch_without_a_call(self, tmp_path, monkeypatch):
        fake = _FakeGraphql(issues={5: _issue_node([[self.LATER_EDIT], [self.ORIGINAL_EDIT]])})
        fetch, _ = self._fetcher(tmp_path, monkeypatch, fake)
        first = fetch([5])
        calls_after_first_fetch = len(fake.calls)
        assert fetch([5]) == first
        assert len(fake.calls) == calls_after_first_fetch

    def test_cache_saved_by_one_run_serves_the_next_run(self, tmp_path, monkeypatch):
        fake = _FakeGraphql(issues={5: _issue_node([[self.ORIGINAL_EDIT]])})
        first_fetch, _ = self._fetcher(tmp_path, monkeypatch, fake)
        first_fetch([5])
        calls_after_first_run = len(fake.calls)
        second_fetch, _ = self._fetcher(tmp_path, monkeypatch, fake)
        assert second_fetch([5])[5] == pool.FirstRevision("original", pool.FIRST_REVISION_OLDEST_EDIT)
        assert len(fake.calls) == calls_after_first_run


class TestGhTransport:
    IDENTITY_JSON = {"nameWithOwner": "owner/repo", "url": "https://github.com/owner/repo"}

    @staticmethod
    def _stub_gh(monkeypatch, stdout, returncode=0, stderr=""):
        """Replaces `subprocess.run` on the shared module, since `pool` reaches it as `subprocess.run`.
        Like the real call, it raises on a non-zero exit under `check=True` and returns no output without `capture_output`."""
        calls = []

        def run(argv, *, check=False, capture_output=False, **kwargs):
            calls.append((list(argv), kwargs))
            if check and returncode != 0:
                raise subprocess.CalledProcessError(returncode, argv, stdout, stderr)
            if not capture_output:
                return subprocess.CompletedProcess(argv, returncode, None, None)
            return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

        monkeypatch.setattr(pool.subprocess, "run", run)
        return calls

    def test_graphql_returns_the_payload_with_errors_beside_data(self, tmp_path, monkeypatch):
        payload = {"data": {"repository": {"i5": None}}, "errors": [{"type": "NOT_FOUND", "path": ["repository", "i5"]}]}
        self._stub_gh(monkeypatch, json.dumps(payload), returncode=1)
        assert pool._graphql(tmp_path, "query", {}) == payload

    @pytest.mark.parametrize(
        "stdout",
        ['{"errors": [{"type": "RATE_LIMITED"}]}', '{"data": null}', "not json", "", "[]", "null", '"x"'],
        ids=["errors-only", "null-data", "not-json", "empty", "array", "null-payload", "string"],
    )
    def test_graphql_without_data_raises(self, tmp_path, monkeypatch, stdout):
        self._stub_gh(monkeypatch, stdout, returncode=1, stderr="boom")
        with pytest.raises(pool.GitHubError, match="boom"):
            pool._graphql(tmp_path, "query", {})

    def test_graphql_passes_the_query_and_every_variable_as_a_raw_field(self, tmp_path, monkeypatch):
        calls = self._stub_gh(monkeypatch, '{"data": {"repository": {}}}')
        pool._graphql(tmp_path, "query", {"owner": "o", "cursor": "@/some/file"})
        argv, _ = calls[0]
        # `-F` would expand an `@file` value into that file's contents and fill a `{placeholder}` value from the repository.
        assert "-F" not in argv
        assert argv[5:] == ["-f", "query=query", "-f", "owner=o", "-f", "cursor=@/some/file"]

    def test_graphql_and_identity_lookup_pass_utf8_encoding_to_gh(self, tmp_path, monkeypatch):
        graphql_calls = self._stub_gh(monkeypatch, '{"data": {"repository": {}}}')
        pool._graphql(tmp_path, "query", {})
        identity_calls = self._stub_gh(monkeypatch, json.dumps(self.IDENTITY_JSON))
        pool.read_repository_identity(tmp_path)
        assert graphql_calls[0][1]["encoding"] == identity_calls[0][1]["encoding"] == "utf-8"

    def test_graphql_targets_github_com_and_drops_ambient_repository_host_and_git_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GH_REPO", "other/repo")
        monkeypatch.setenv("GH_HOST", "ghe.example.test")
        monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere"))
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("GH_TOKEN", "fake-gh-token")
        monkeypatch.setenv("GITHUB_TOKEN", "fake-github-token")
        calls = self._stub_gh(monkeypatch, '{"data": {"repository": {}}}')
        pool._graphql(tmp_path, "query", {"owner": "o"})
        argv, kwargs = calls[0]
        assert argv[:5] == ["gh", "api", "graphql", "--hostname", "github.com"]
        assert "GH_REPO" not in kwargs["env"]
        assert "GH_HOST" not in kwargs["env"]
        assert "GIT_DIR" not in kwargs["env"]
        assert kwargs["env"]["HOME"] == str(tmp_path)
        assert kwargs["env"]["PATH"]
        assert kwargs["env"]["GH_TOKEN"] == "fake-gh-token"
        assert kwargs["env"]["GITHUB_TOKEN"] == "fake-github-token"

    def test_repository_identity_lookup_reads_name_and_url_and_drops_ambient_overrides(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GH_REPO", "other/repo")
        monkeypatch.setenv("GH_HOST", "ghe.example.test")
        monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere"))
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("GH_TOKEN", "fake-gh-token")
        monkeypatch.setenv("GITHUB_TOKEN", "fake-github-token")
        calls = self._stub_gh(monkeypatch, json.dumps(self.IDENTITY_JSON))
        assert pool.read_repository_identity(tmp_path) == pool.RepositoryIdentity("owner/repo", "https://github.com/owner/repo")
        argv, kwargs = calls[0]
        assert argv[-1] == "nameWithOwner,url"
        assert "GH_REPO" not in kwargs["env"]
        assert "GH_HOST" not in kwargs["env"]
        assert "GIT_DIR" not in kwargs["env"]
        assert kwargs["env"]["HOME"] == str(tmp_path)
        assert kwargs["env"]["PATH"]
        assert kwargs["env"]["GH_TOKEN"] == "fake-gh-token"
        assert kwargs["env"]["GITHUB_TOKEN"] == "fake-github-token"

    def test_repository_identity_lookup_runs_in_the_checkout_it_is_given(self, tmp_path, monkeypatch):
        calls = self._stub_gh(monkeypatch, json.dumps(self.IDENTITY_JSON))
        pool.read_repository_identity(tmp_path)
        _, kwargs = calls[0]
        assert kwargs["cwd"] == tmp_path

    @pytest.mark.parametrize(
        ("stdout", "returncode"),
        [
            (json.dumps(IDENTITY_JSON), 1),
            (json.dumps({**IDENTITY_JSON, "nameWithOwner": "not a repository name"}), 0),
            (json.dumps({"nameWithOwner": "owner/repo"}), 0),
            (json.dumps({**IDENTITY_JSON, "url": None}), 0),
            ("[]", 0),
            ("not json", 0),
            ("", 0),
        ],
        ids=["gh-failed", "unexpected-name", "no-url", "null-url", "not-an-object", "not-json", "empty"],
    )
    def test_repository_identity_lookup_raises_on_failure_or_a_malformed_answer(self, tmp_path, monkeypatch, stdout, returncode):
        self._stub_gh(monkeypatch, stdout, returncode=returncode, stderr="no remote")
        with pytest.raises(pool.GitHubError):
            pool.read_repository_identity(tmp_path)


class TestMain:
    UNEDITED_ISSUE = _issue_node([[]], last_edited_at=None)
    TOOL_SOURCE = "def f():\n    # why\n    return {value}\n\n\ndef g():\n    return 2\n"

    @staticmethod
    def _history(tmp_path, *, with_study_page):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "tool.py", TestMain.TOOL_SOURCE.format(value=1))
        _commit(repo, "initial")
        _write(repo, "tool.py", TestMain.TOOL_SOURCE.format(value=10))
        _commit(repo, "GH-5 change f (#40)")
        study = None
        if with_study_page:
            _write(repo, "docs/case-studies/comments-stripped-ab.md", "page\n")
            study = _commit(repo, "add the study page (#41)")
        _write(repo, "tool.py", TestMain.TOOL_SOURCE.format(value=11))
        _commit(repo, "GH-5 change f again (#42)")
        return repo, study

    @staticmethod
    def _fake_github():
        return _FakeGraphql(
            pull_requests={
                40: _pull_request_node("branch-40", title="GH-5 change f"),
                41: _pull_request_node("branch-41", title="add the study page"),
                42: _pull_request_node("branch-42", title="GH-5 change f again"),
            },
            issues={5: TestMain.UNEDITED_ISSUE},
        )

    @staticmethod
    def _run(tmp_path, monkeypatch, capsys, repo, fake, repository, url=None, extra_args=()):
        identity = pool.RepositoryIdentity(repository, url or f"https://{pool.GITHUB_HOSTNAME}/{repository}")
        identity_lookups = []

        def read_identity(checkout):
            identity_lookups.append(checkout)
            return identity

        monkeypatch.setattr(pool, "read_repository_identity", read_identity)
        monkeypatch.setattr(pool, "_graphql", fake)
        exit_code = pool.main(["--repo", str(repo), "--ref", "HEAD", "--cache-dir", str(tmp_path / "cache"), *extra_args])
        assert identity_lookups == [repo]
        return exit_code, capsys.readouterr()

    @staticmethod
    def _forbid_any_work_after_the_identity_lookup(monkeypatch):
        """Makes every git read and the cache construction raise, so a refusal that runs any of them fails the test."""

        def forbidden(*args, **kwargs):
            raise AssertionError("work ran after the identity lookup but before the refusal")

        for name in (
            "read_first_parent_commits",
            "find_study_start_commit",
            "read_ancestors_before",
            "git_names_commit",
            "git_why_comment_judge",
            "GitHubCache",
        ):
            monkeypatch.setattr(pool, name, forbidden)

    @staticmethod
    def _json_report(stdout):
        return json.loads(stdout[stdout.index('{\n  "label"') :])

    def test_refuses_any_repository_but_the_study_repository_before_any_graphql_call(self, tmp_path, monkeypatch, capsys):
        fake = self._fake_github()
        self._forbid_any_work_after_the_identity_lookup(monkeypatch)
        exit_code, captured = self._run(tmp_path, monkeypatch, capsys, tmp_path, fake, "other/repo")
        assert exit_code == 1
        assert fake.calls == []
        assert captured.out == ""
        assert len(captured.err.strip().splitlines()) == 1
        assert pool.STUDY_REPOSITORY_URL in captured.err
        assert "other/repo" not in captured.err

    def test_refuses_the_study_repository_name_on_another_host_before_any_graphql_call(self, tmp_path, monkeypatch, capsys):
        fake = self._fake_github()
        other_host_url = f"https://ghe.example.test/{pool.STUDY_REPOSITORY}"
        self._forbid_any_work_after_the_identity_lookup(monkeypatch)
        exit_code, captured = self._run(tmp_path, monkeypatch, capsys, tmp_path, fake, pool.STUDY_REPOSITORY, url=other_host_url)
        assert exit_code == 1
        assert fake.calls == []
        assert captured.out == ""
        assert len(captured.err.strip().splitlines()) == 1
        assert pool.STUDY_REPOSITORY_URL in captured.err
        assert "ghe.example.test" not in captured.err

    def test_refuses_a_shallow_checkout_before_any_graphql_call(self, tmp_path, monkeypatch, capsys):
        repo, _ = self._history(tmp_path, with_study_page=True)
        shallow_clone = tmp_path / "shallow-clone"
        _git(tmp_path, "clone", "-q", "--depth", "1", f"file://{repo}", str(shallow_clone))
        fake = self._fake_github()
        self._forbid_any_work_after_the_identity_lookup(monkeypatch)
        exit_code, captured = self._run(tmp_path, monkeypatch, capsys, shallow_clone, fake, pool.STUDY_REPOSITORY)
        assert exit_code == 1
        assert fake.calls == []
        assert captured.out == ""
        assert len(captured.err.strip().splitlines()) == 1
        assert "shallow" in captured.err

    def test_counts_the_study_repository_and_stops_stage_five_at_the_study_start(self, tmp_path, monkeypatch, capsys):
        repo, study = self._history(tmp_path, with_study_page=True)
        fake = self._fake_github()
        exit_code, captured = self._run(tmp_path, monkeypatch, capsys, repo, fake, pool.STUDY_REPOSITORY)
        report = self._json_report(captured.out)
        assert exit_code == 0
        _assert_every_query_targets(fake, pool.STUDY_REPOSITORY)
        counts = {stage["id"]: stage["count"] for stage in report["stages"]}
        assert counts == {"1": 3, "2": 2, "3": 2, "4": 2, "5": 1, "6": 1, "7": 1}
        assert report["surviving_pull_requests"] == [40]
        assert report["context"]["repository"].startswith(pool.STUDY_REPOSITORY)
        assert "history under --ref not verified" in report["context"]["repository"]
        assert report["context"]["first_study_artifact_commit"] == study[:12]
        human_report = captured.out[: captured.out.index('{\n  "label"')]
        assert human_report.rstrip().splitlines()[-1] == pool.comparison_line(1)
        assert any(line.startswith(f"repository: {pool.STUDY_REPOSITORY} ") for line in human_report.splitlines())

    def test_passes_every_base_at_stage_five_when_the_ref_holds_no_study_artifact(self, tmp_path, monkeypatch, capsys):
        repo, _ = self._history(tmp_path, with_study_page=False)
        exit_code, captured = self._run(tmp_path, monkeypatch, capsys, repo, self._fake_github(), pool.STUDY_REPOSITORY)
        report = self._json_report(captured.out)
        assert exit_code == 0
        counts = {stage["id"]: stage["count"] for stage in report["stages"]}
        assert counts == {"1": 2, "2": 2, "3": 2, "4": 2, "5": 2, "6": 2, "7": 2}
        assert sorted(report["surviving_pull_requests"]) == [40, 42]
        assert report["context"]["first_study_artifact_commit"].startswith("none")

    def test_second_run_answers_from_the_cache_without_a_graphql_call(self, tmp_path, monkeypatch, capsys):
        repo, _ = self._history(tmp_path, with_study_page=True)
        fake = self._fake_github()
        self._run(tmp_path, monkeypatch, capsys, repo, fake, pool.STUDY_REPOSITORY)
        calls_after_first_run = len(fake.calls)
        exit_code, _ = self._run(tmp_path, monkeypatch, capsys, repo, fake, pool.STUDY_REPOSITORY)
        assert exit_code == 0
        assert len(fake.calls) == calls_after_first_run

    def test_refresh_flag_fetches_again_past_a_populated_cache(self, tmp_path, monkeypatch, capsys):
        repo, _ = self._history(tmp_path, with_study_page=True)
        fake = self._fake_github()
        self._run(tmp_path, monkeypatch, capsys, repo, fake, pool.STUDY_REPOSITORY)
        calls_after_first_run = len(fake.calls)
        exit_code, _ = self._run(tmp_path, monkeypatch, capsys, repo, fake, pool.STUDY_REPOSITORY, extra_args=("--refresh",))
        assert exit_code == 0
        assert len(fake.calls) == 2 * calls_after_first_run
