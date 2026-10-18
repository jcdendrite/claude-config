"""Tests for the `gh pr list` double in _review_pr_stack_helpers.py.

Every suite that answers the stack walk's lookups through the double relies on
it behaving like gh for the one call form the walk issues, so the double's own
contract is pinned here: argument parsing, projection and the reply kinds.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from ._review_pr_stack_helpers import (
    KNOWN_JSON_FIELDS,
    UNMODELED_CALL_EXIT_STATUS,
    answer_gh_pr_list,
    logged_gh_calls,
    pr_list_item,
    write_gh_pr_list_shim,
)

_OWNER_REPO = "example-org/example-repo"


def _lookup_args(head_value: str = "feat/x", *, fields: str = "number,headRefName") -> list[str]:
    """argv after `gh pr list`, in the form the stack walk issues."""
    return ["-R", _OWNER_REPO, f"--head={head_value}", "--state", "open", "--limit", "100", "--json", fields]


def _answer(args: list[str], replies: dict, capsys) -> tuple[int, str, str]:
    status = answer_gh_pr_list(args, replies)
    captured = capsys.readouterr()
    return status, captured.out, captured.err


class TestProjection:
    def test_each_item_keeps_only_the_requested_fields(self, capsys):
        replies = {"feat/x": [pr_list_item(11, "feat/x", "main", title="Add the parser")]}

        status, stdout, _ = _answer(_lookup_args(fields="number,title"), replies, capsys)

        assert status == 0
        assert json.loads(stdout) == [{"number": 11, "title": "Add the parser"}]

    def test_a_field_outside_the_known_set_exits_1_naming_the_field(self, capsys):
        status, stdout, stderr = _answer(_lookup_args(fields="number,bogus"), {"feat/x": []}, capsys)

        assert status == 1
        assert stdout == ""
        assert "Unknown JSON field: bogus" in stderr


class TestHeadMatching:
    @pytest.mark.parametrize(
        ("answering_key", "requested_head"),
        [("feat/x", "feat/x "), ("feat/x ", "feat/x"), ("Main", "main"), ("main", "Main")],
        ids=["trailing-space-requested", "trailing-space-key", "capitalized-key", "capitalized-request"],
    )
    def test_a_head_value_differing_in_one_character_is_a_different_key(self, answering_key, requested_head, capsys):
        replies = {answering_key: [pr_list_item(11, answering_key, "dev")]}

        status, stdout, _ = _answer(_lookup_args(requested_head), replies, capsys)

        assert status == 0
        assert json.loads(stdout) == []

    def test_a_branch_with_no_key_answers_an_empty_list(self, capsys):
        status, stdout, _ = _answer(_lookup_args("never-listed"), {"feat/x": [pr_list_item(11, "feat/x", "main")]}, capsys)

        assert (status, json.loads(stdout)) == (0, [])


class TestReplyKinds:
    def test_raw_stdout_prints_verbatim_and_exits_0(self, capsys):
        status, stdout, stderr = _answer(_lookup_args(), {"feat/x": {"stdout": "not json"}}, capsys)

        assert (status, stdout, stderr) == (0, "not json", "")

    def test_exit_reply_exits_with_its_status_and_no_stdout(self, capsys):
        status, stdout, stderr = _answer(_lookup_args(), {"feat/x": {"exit": 4}}, capsys)

        assert (status, stdout, stderr) == (4, "", "")

    def test_exit_reply_with_stdout_prints_it_before_exiting_nonzero(self, capsys):
        status, stdout, stderr = _answer(_lookup_args(), {"feat/x": {"exit": 3, "stdout": "[]"}}, capsys)

        assert (status, stdout, stderr) == (3, "[]", "")

    def test_exit_reply_prints_its_stderr_text_first(self, capsys):
        status, stdout, stderr = _answer(_lookup_args(), {"feat/x": {"exit": 1, "stderr": "sentinel text\n"}}, capsys)

        assert (status, stdout, stderr) == (1, "", "sentinel text\n")

    def test_sleep_reply_sleeps_before_answering(self, capsys, monkeypatch):
        slept: list[float] = []
        monkeypatch.setattr("time.sleep", slept.append)

        status, stdout, _ = _answer(_lookup_args(), {"feat/x": {"sleep": 7, "stdout": "[]"}}, capsys)

        assert (status, stdout, slept) == (0, "[]", [7])

    def test_a_reply_that_is_neither_a_list_nor_a_dict_is_not_modeled(self, capsys):
        status, _, stderr = _answer(_lookup_args(), {"feat/x": "oops"}, capsys)

        assert status == UNMODELED_CALL_EXIT_STATUS
        assert "neither a list nor a dict" in stderr


class TestUnmodeledCalls:
    @pytest.mark.parametrize(
        "args",
        [
            ["-R", _OWNER_REPO, "--head", "feat/x", "--state", "open", "--limit", "100", "--json", "number"],
            ["-R", _OWNER_REPO, "--state", "open", "--limit", "100", "--json", "number"],
            ["--head=feat/x", "--state", "open", "--limit", "100", "--json", "number"],
            ["-R", _OWNER_REPO, "--head=feat/x", "--limit", "100", "--json", "number"],
            ["-R", _OWNER_REPO, "--head=feat/x", "--state", "open", "--json", "number"],
            ["-R", _OWNER_REPO, "--head=feat/x", "--state", "open", "--limit", "100"],
            ["-R", _OWNER_REPO, "--head=feat/x", "--state", "open", "--limit", "100", "--json", "number", "--author", "a"],
            ["-R", _OWNER_REPO, "--head=feat/x", "--head=feat/y", "--state", "open", "--limit", "100", "--json", "number"],
            ["-R", _OWNER_REPO, "--head=feat/x", "--state", "merged", "--limit", "100", "--json", "number"],
            ["-R", _OWNER_REPO, "--head=feat/x", "--state", "all", "--limit", "100", "--json", "number"],
            ["-R", _OWNER_REPO, "--head=", "--state", "open", "--limit", "100", "--json", "number"],
            ["-R", _OWNER_REPO, "--head=feat/x", "--state", "open", "--limit", "0", "--json", "number"],
            ["-R", "no-slash", "--head=feat/x", "--state", "open", "--limit", "100", "--json", "number"],
            ["-R", _OWNER_REPO, "--head=feat/x", "--state", "open", "--limit", "100", "--json"],
        ],
        ids=[
            "two-token-head", "missing-head", "missing-repo-flag", "missing-state", "missing-limit", "missing-json",
            "extra-flag", "head-given-twice", "state-merged", "state-all", "empty-head", "zero-limit", "repo-without-slash",
            "json-without-value",
        ],
    )
    def test_a_call_outside_the_modeled_form_exits_97_with_a_stderr_line(self, args, capsys):
        status, stdout, stderr = _answer(args, {"feat/x": [pr_list_item(11, "feat/x", "main")]}, capsys)

        assert status == UNMODELED_CALL_EXIT_STATUS
        assert stdout == ""
        assert stderr.startswith("gh pr list double:")


class TestMalformedReplyItems:
    @pytest.mark.parametrize(
        "malformed_item",
        [{"number": 11}, "not an object", None],
        ids=["item-lacks-a-requested-field", "item-is-a-string", "item-is-null"],
    )
    def test_a_reply_item_that_is_not_a_complete_object_exits_97_with_a_double_stderr_line_not_a_traceback(
        self, malformed_item, capsys
    ):
        status, stdout, stderr = _answer(_lookup_args(fields="number,title"), {"feat/x": [malformed_item]}, capsys)

        assert status == UNMODELED_CALL_EXIT_STATUS
        assert stdout == ""
        assert stderr.startswith("gh pr list double: reply item for --head=feat/x")


class TestItemBuilders:
    def test_a_default_item_carries_every_known_json_field(self):
        item = pr_list_item(11, "feat/x", "main")

        assert set(item) == KNOWN_JSON_FIELDS


class TestShim:
    def test_shim_logs_argv_answers_pr_list_and_rejects_other_calls(self, tmp_path):
        call_log = tmp_path / "calls.log"
        shim = write_gh_pr_list_shim(tmp_path, call_log, {"feat/x": [pr_list_item(11, "feat/x", "main")]})

        listing = subprocess.run(
            [str(shim), "pr", "list", *_lookup_args(fields="number")],
            capture_output=True, text=True, check=False, timeout=60,
        )
        other = subprocess.run(
            [str(shim), "pr", "view", "5"], capture_output=True, text=True, check=False, timeout=60,
        )

        assert (listing.returncode, json.loads(listing.stdout)) == (0, [{"number": 11}])
        assert other.returncode == UNMODELED_CALL_EXIT_STATUS
        assert logged_gh_calls(call_log) == [["pr", "list", *_lookup_args(fields="number")], ["pr", "view", "5"]]

    def test_logged_gh_calls_is_empty_before_any_call(self, tmp_path):
        assert logged_gh_calls(tmp_path / "never-written.log") == []
