"""Unit tests for conftest.py's `_assert_gh_calls_are_read_only`, the one
write-guard the review-pr acquire, checkout and diff suites share. None of
those scripts makes a write call today, so only a test of the guard itself
shows it would reject one.
"""
from __future__ import annotations

import re

import pytest

from .conftest import _assert_gh_calls_are_read_only

_KNOWN_API_ENDPOINT = re.compile(r"repos/[a-z]+/[a-z]+/pulls/[0-9]+(/files\?per_page=100)?")

# `pytest.fail` raises a BaseException subclass, which `AssertionError` does not cover.
_GUARD_REJECTION = (AssertionError, pytest.fail.Exception)


def _guard(call: list[str], **kwargs) -> None:
    _assert_gh_calls_are_read_only([call], known_api_endpoint=_KNOWN_API_ENDPOINT, **kwargs)


class TestProductionCallShapesAreAccepted:
    @pytest.mark.parametrize(
        "call",
        [
            ["api", "repos/foo/bar/pulls/42"],
            ["api", "repos/foo/bar/pulls/42/files?per_page=100", "--paginate", "--jq", ".[].filename | @json"],
            ["pr", "view", "42", "-R", "foo/bar", "--json", "headRefOid", "--jq", ".headRefOid"],
        ],
        ids=["api_endpoint", "api_paginated_listing_with_a_jq_filter", "pr_view"],
    )
    def test_the_guard_accepts_the_call(self, call):
        _guard(call)

    def test_pr_diff_is_accepted_when_the_suite_lists_it(self):
        _guard(["pr", "diff", "42", "-R", "foo/bar"], allowed_pr_subcommands=("view", "diff"))


class TestAnythingBeyondTheProductionShapesIsRejected:
    @pytest.mark.parametrize(
        "call",
        [
            ["api", "repos/foo/bar/pulls/42", "--method", "GET"],
            ["api", "repos/foo/bar/pulls/42/files?per_page=100", "--paginate", "--jq", ".[]", "extra"],
            ["api", "repos/foo/bar/pulls/42", "-XPOST"],
            ["api", "repos/foo/bar/pulls/42/reviews"],
            ["api", "repos/foo/bar/pulls/42", "--paginate", "-f", "body=x"],
        ],
        ids=[
            "one_extra_flag",
            "one_extra_token_after_the_jq_filter",
            "glued_short_flag",
            "endpoint_with_an_allowlisted_prefix_and_a_suffix",
            "three_trailing_tokens_that_are_not_paginate_jq_filter",
        ],
    )
    def test_the_guard_rejects_the_call(self, call):
        with pytest.raises(_GUARD_REJECTION):
            _guard(call)

    @pytest.mark.parametrize(
        "call",
        [
            ["pr", "merge", "42", "-R", "foo/bar"],
            ["pr", "comment", "42", "-R", "foo/bar", "--body", "x"],
            ["pr", "review", "42", "-R", "foo/bar", "--comment", "-F", "-"],
            ["pr", "diff", "42", "-R", "foo/bar"],
            ["pr", "view", "42", "--json", "headRefOid"],
            ["pr", "view", "42", "-R", "foo/bar"],
        ],
        ids=[
            "pr_merge_under_the_default_subcommands",
            "pr_comment_under_the_default_subcommands",
            "pr_review_under_the_default_subcommands",
            "pr_diff_when_the_suite_does_not_list_it",
            "pr_view_missing_the_repo_flag",
            "pr_view_missing_the_json_flag",
        ],
    )
    def test_the_guard_rejects_the_pr_call(self, call):
        with pytest.raises(_GUARD_REJECTION):
            _guard(call)
