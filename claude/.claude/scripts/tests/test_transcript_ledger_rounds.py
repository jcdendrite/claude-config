"""Tests for transcript_analysis/ledger_rounds.py: select_ledger_prs's selection policy over parsed rows, and
read_ledger_prs's ledger reading and fixed-message failures."""
import os
from datetime import timedelta
from pathlib import Path

import pytest
from transcript_analysis import ledger_rounds, pr_cost, pr_cost_ledger, pricing, review_rounds, scope

from ._pr_cost_helpers import (
    _LEDGER_DIR_MARKER,
    _PRE_REVIEW_ROUNDS_HEADER_LINE,
    _current_line_with_cell,
    _ledger_pr_row,
    _pr_cost_args,
    _pre_review_rounds_row_line,
    _review_rounds_cell,
    _round_with_priced_splits,
)
from ._review_round_corpus_helpers import _priced_with_classes
from .conftest import _agent_use, _skill_block, _user_msg, _write_jsonl, _write_subagent_dispatch

_HOST = "github.com"
_REPO = "owner/repo"
_SUBCOMMAND = "review-cost-by-size"
_UNPRICED_MODEL = "zz-unpriced-model-sentinel"
_LEDGER_PATH_ENV = "PR_COST_LEDGER_PATH"
_CELL_SENTINEL = "ZZ-CELL-LEAK-SENTINEL"
_PARSE_ERROR_SUFFIX = ' -- see docs/pr-cost.md § "Data"'
_REFUSED_ACROSS_ROOTS_MESSAGE = (
    f"{_SUBCOMMAND}: PR_COST_LEDGER_PATH is refused when more than one root resolves -- unset it (each account then"
    " reads its own ledger) or drop --all-accounts"
)


def _round(*, main_input_tokens: int = 1_000, **overrides) -> dict:
    """A priceable round whose main and agent token splits differ, so a swapped field changes its dollars."""
    return _round_with_priced_splits(
        {"standard": {"input": main_input_tokens}}, {"fast": {"output": main_input_tokens * 3}}, **overrides,
    )


def _code_review_cell(code_review: list[dict] | None = None, **other_skills: list[dict]) -> dict:
    """A cell whose code-review list is `code_review`, or one priceable round when None."""
    return _review_rounds_cell({"code-review": [_round()] if code_review is None else code_review, **other_skills})


def _cell_with_an_after_merge_round(*rounds: dict) -> dict:
    """A cell whose code-review list holds an after-merge round and then `rounds` (one priceable round by default), so an
    exclusion that reached the after-merge round before deciding would count it."""
    return _code_review_cell([_round(after_merge=1), *(rounds or [_round()])])


def _row(pr_number: int = 1, *, machine: str = "ma", cell: dict | None = None, **overrides) -> dict:
    return _ledger_pr_row(pr_number, machine=machine, cell=cell, **{"host": _HOST, "repo": _REPO, **overrides})


def _select(*rows_by_account: list[dict]) -> ledger_rounds.LedgerRead:
    return ledger_rounds.select_ledger_prs(list(rows_by_account), _HOST, _REPO)


def _round_usd(review_round: dict) -> float:
    return pricing._reprice_token_splits(review_round["main_tokens"]) + pricing._reprice_token_splits(
        review_round["agent_tokens"]
    )


def _assert_only_this_reason_counted(read: ledger_rounds.LedgerRead, reason: str) -> None:
    """Also asserts the excluded PR's after-merge round went uncounted, so the removal cannot run before the exclusion."""
    assert read.prs == []
    assert read.after_merge_rounds == {}
    assert read.excluded == {each: int(each == reason) for each in ledger_rounds.EXCLUSION_REASONS}


class TestExclusionReasons:
    def test_labels_are_distinct_and_excluded_counts_follow_their_order(self):
        assert len(set(ledger_rounds.EXCLUSION_REASONS)) == len(ledger_rounds.EXCLUSION_REASONS)
        assert list(_select().excluded) == list(ledger_rounds.EXCLUSION_REASONS)

    def test_the_multi_row_label_points_at_the_docs_for_the_cause_list(self):
        assert ledger_rounds.EXCLUDED_MULTI_ROW == "more than one row with a review_rounds cell, see docs/transcript-analysis.md"


class TestSelectLedgerPrsFilter:
    def test_a_row_for_another_host_is_never_returned_or_counted(self):
        read = _select([_row(1, cell=_code_review_cell(), host="ghe.example")])
        assert (read.prs, read.pr_count) == ([], 0)

    def test_a_row_for_another_repo_on_this_host_is_never_returned_or_counted(self):
        read = _select([_row(1, cell=_code_review_cell(), repo="owner/other")])
        assert (read.prs, read.pr_count) == ([], 0)

    def test_a_superseded_row_is_ignored(self):
        older = _row(1, cell=_code_review_cell(), captured_at="2026-01-02T00:00:00Z", additions=1, deletions=1)
        newer = _row(1, cell=_code_review_cell(), captured_at="2026-01-03T00:00:00Z", additions=70, deletions=3)
        read = _select([older, newer])
        assert [pr.lines for pr in read.prs] == [73]
        assert read.pr_count == 1

    def test_a_newer_degraded_correction_over_an_older_ok_row_is_the_row_read(self):
        ok_row = _row(1, cell=_code_review_cell(), captured_at="2026-01-02T00:00:00Z", additions=1, deletions=1)
        degraded = _row(
            1, cell=_code_review_cell(), captured_at="2026-01-03T00:00:00Z", additions=70, deletions=3,
            status=pr_cost_ledger._PR_COST_STATUS_DEGRADED_NETWORK,
        )
        assert [pr.lines for pr in _select([ok_row, degraded]).prs] == [73]

    def test_prs_come_back_in_pr_number_order_across_accounts(self):
        read = _select(
            [_row(5, cell=_code_review_cell())], [_row(2, cell=_code_review_cell()), _row(9, cell=_code_review_cell())],
        )
        assert [pr.number for pr in read.prs] == [2, 5, 9]
        assert read.pr_count == 3


class TestSelectLedgerPrsExclusions:
    @pytest.mark.parametrize(
        ("reason", "rows"),
        [
            pytest.param(ledger_rounds.EXCLUDED_NO_CELL, [_row(1)], id="no-cell"),
            pytest.param(
                ledger_rounds.EXCLUDED_MULTI_ROW,
                [
                    _row(1, machine="ma", cell=_cell_with_an_after_merge_round()),
                    _row(1, machine="mb", cell=_cell_with_an_after_merge_round()),
                ],
                id="multi-row",
            ),
            pytest.param(
                ledger_rounds.EXCLUDED_JOIN_CONFIDENCE_LOW,
                [_row(
                    1, cell=_cell_with_an_after_merge_round(),
                    join_confidence=pr_cost_ledger._PR_COST_JOIN_CONFIDENCE_LOW,
                )],
                id="join-confidence-low",
            ),
            pytest.param(
                ledger_rounds.EXCLUDED_RULE,
                [_row(1, cell={**_cell_with_an_after_merge_round(), "rule": review_rounds.REVIEW_ROUND_RULE + 1})],
                id="rule",
            ),
            pytest.param(
                ledger_rounds.EXCLUDED_RULE,
                [_row(1, cell={
                    **_cell_with_an_after_merge_round(), "rule": review_rounds.REVIEW_ROUND_RULE - 1,
                })],
                id="older-rule",
            ),
            pytest.param(
                ledger_rounds.EXCLUDED_UNPRICEABLE_ROUND,
                [_row(1, cell=_cell_with_an_after_merge_round(
                    _round(main_tokens={_UNPRICED_MODEL: {"standard": {"input": 1}}}),
                ))],
                id="unpriceable-round",
            ),
        ],
    )
    def test_each_reason_alone_excludes_its_pr_and_moves_only_its_own_count(self, reason, rows):
        read = _select(rows)
        _assert_only_this_reason_counted(read, reason)
        assert read.pr_count == 1

    def test_join_confidence_and_rule_together_count_once_under_join_confidence(self):
        row = _row(
            1, cell={**_cell_with_an_after_merge_round(), "rule": review_rounds.REVIEW_ROUND_RULE + 1},
            join_confidence=pr_cost_ledger._PR_COST_JOIN_CONFIDENCE_LOW,
        )
        _assert_only_this_reason_counted(_select([row]), ledger_rounds.EXCLUDED_JOIN_CONFIDENCE_LOW)

    def test_two_machines_one_of_them_low_confidence_counts_once_under_multi_row(self):
        low_confidence = _row(
            1, machine="ma", cell=_cell_with_an_after_merge_round(),
            join_confidence=pr_cost_ledger._PR_COST_JOIN_CONFIDENCE_LOW,
        )
        second_machine = _row(1, machine="mb", cell=_cell_with_an_after_merge_round())
        _assert_only_this_reason_counted(_select([low_confidence, second_machine]), ledger_rounds.EXCLUDED_MULTI_ROW)

    def test_a_wrong_rule_with_an_unpriceable_round_counts_once_under_rule(self):
        unpriceable = _round(main_tokens={_UNPRICED_MODEL: {"standard": {"input": 1}}})
        row = _row(
            1, cell={**_cell_with_an_after_merge_round(unpriceable), "rule": review_rounds.REVIEW_ROUND_RULE + 1},
        )
        _assert_only_this_reason_counted(_select([row]), ledger_rounds.EXCLUDED_RULE)

    def test_the_same_pr_and_machine_in_two_accounts_is_excluded_once_as_multi_row(self):
        read = _select(
            [_row(1, machine="ma", cell=_cell_with_an_after_merge_round())],
            [_row(1, machine="ma", cell=_cell_with_an_after_merge_round())],
        )
        _assert_only_this_reason_counted(read, ledger_rounds.EXCLUDED_MULTI_ROW)
        assert read.pr_count == 1

    def test_a_cell_less_row_beside_a_cell_carrying_row_leaves_the_pr_kept(self):
        read = _select([_row(1, machine="ma"), _row(1, machine="mb", cell=_code_review_cell())])
        assert [pr.number for pr in read.prs] == [1]
        assert read.excluded == dict.fromkeys(ledger_rounds.EXCLUSION_REASONS, 0)

    @pytest.mark.parametrize(
        "status", [each for each in pr_cost_ledger._PR_COST_STATUS_VALUES if each != pr_cost_ledger._PR_COST_STATUS_OK],
    )
    def test_a_degraded_row_is_kept_with_its_size_from_additions_plus_deletions(self, status):
        read = _select([_row(1, cell=_code_review_cell(), status=status, additions=37, deletions=5)])
        assert [(pr.number, pr.lines) for pr in read.prs] == [(1, 42)]

    def test_a_row_with_negative_additions_plus_deletions_is_kept_with_that_negative_size(self):
        read = _select([_row(1, cell=_code_review_cell(), additions=-30, deletions=10)])
        assert [(pr.number, pr.lines) for pr in read.prs] == [(1, -20)]

    def test_medium_join_confidence_is_kept(self):
        row = _row(1, cell=_code_review_cell(), join_confidence=pr_cost_ledger._PR_COST_JOIN_CONFIDENCE_MEDIUM)
        assert [pr.number for pr in _select([row]).prs] == [1]

    def test_a_row_stamped_a_day_before_the_current_rate_table_is_kept_and_repriced(self):
        stamp = (pricing._PRICING_FETCH_DATE - timedelta(days=1)).isoformat()
        review_round = _round(main_usd_micros=1, agent_usd_micros=1)
        read = _select([_row(1, cell=_code_review_cell([review_round]), rate_stamp=stamp)])
        assert read.prs[0].usd_by_skill["code-review"] == [pytest.approx(_round_usd(review_round))]


class TestSelectLedgerPrsRounds:
    def test_usd_by_skill_sums_each_rounds_two_repriced_splits_in_list_order(self):
        first = _round(main_input_tokens=250_000)
        second = _round(main_input_tokens=1_000)
        third = _round(main_input_tokens=70_000)
        plan = _round(main_input_tokens=70_000)
        # The stored micro-dollars differ from the splits' price, so only a re-price gives the expected figure.
        for each in (first, second, third, plan):
            each["main_usd_micros"] = 123
        read = _select([_row(1, cell=_code_review_cell([first, second, third], **{"plan-review": [plan]}))])
        assert list(read.prs[0].usd_by_skill) == ["code-review", "plan-review"]
        # The series is neither ascending nor descending, so list order differs from sorted order.
        assert _round_usd(second) < _round_usd(third) < _round_usd(first)
        assert read.prs[0].usd_by_skill["code-review"] == [
            pytest.approx(_round_usd(first)), pytest.approx(_round_usd(second)), pytest.approx(_round_usd(third)),
        ]
        assert read.prs[0].usd_by_skill["plan-review"] == [pytest.approx(_round_usd(plan))]

    def test_a_round_with_no_agent_tokens_is_kept_and_priced_at_its_main_price(self):
        no_agent = _round(agent_tokens={})
        read = _select([_row(1, cell=_code_review_cell([no_agent]))])
        assert read.excluded == dict.fromkeys(ledger_rounds.EXCLUSION_REASONS, 0)
        main_usd = pricing._reprice_token_splits(no_agent["main_tokens"])
        assert main_usd > 0
        assert read.prs[0].usd_by_skill["code-review"] == [pytest.approx(main_usd)]

    def test_a_round_with_neither_main_nor_agent_tokens_is_kept_at_zero_dollars(self):
        empty = _round(main_tokens={}, agent_tokens={})
        read = _select([_row(1, cell=_code_review_cell([empty]))])
        assert read.excluded == dict.fromkeys(ledger_rounds.EXCLUSION_REASONS, 0)
        assert read.prs[0].usd_by_skill["code-review"] == [0.0]

    def test_an_unpriceable_after_merge_round_does_not_exclude_its_pr(self):
        unpriceable = _round(main_tokens={_UNPRICED_MODEL: {"standard": {"input": 1}}}, after_merge=1)
        read = _select([_row(1, cell=_code_review_cell([_round(), unpriceable]))])
        assert [pr.number for pr in read.prs] == [1]
        assert read.excluded[ledger_rounds.EXCLUDED_UNPRICEABLE_ROUND] == 0

    def test_an_unpriceable_round_in_another_skills_list_excludes_the_pr(self):
        unpriceable = _round(agent_tokens={_UNPRICED_MODEL: {"standard": {"output": 1}}})
        read = _select([_row(1, cell=_code_review_cell([_round()], **{"plan-review": [unpriceable]}))])
        _assert_only_this_reason_counted(read, ledger_rounds.EXCLUDED_UNPRICEABLE_ROUND)

    def test_after_merge_rounds_are_removed_and_counted_per_skill_leaving_the_rest_in_order(self):
        before_first, before_second = _round(main_input_tokens=1_000), _round(main_input_tokens=2_000)
        after_merge = _round(main_input_tokens=3_000, after_merge=1)
        plan_before, plan_after = _round(main_input_tokens=4_000), _round(main_input_tokens=5_000, after_merge=1)
        cell = _code_review_cell(
            [before_first, after_merge, before_second], **{"plan-review": [plan_before, plan_after]},
        )
        read = _select([_row(1, cell=cell)])
        assert read.prs[0].rounds_by_skill == {"code-review": [before_first, before_second], "plan-review": [plan_before]}
        assert read.prs[0].usd_by_skill["code-review"] == [
            pytest.approx(_round_usd(before_first)), pytest.approx(_round_usd(before_second)),
        ]
        assert read.after_merge_rounds == {"code-review": 1, "plan-review": 1}

    def test_a_round_flagged_zero_or_with_no_flag_is_kept(self):
        flagged_zero = _round(after_merge=0)
        no_flag = {key: value for key, value in _round().items() if key != "after_merge"}
        read = _select([_row(1, cell=_code_review_cell([flagged_zero, no_flag]))])
        assert read.prs[0].rounds_by_skill["code-review"] == [flagged_zero, no_flag]
        assert read.after_merge_rounds == {}

    def test_a_skill_whose_rounds_are_all_after_merge_stays_as_an_empty_list(self):
        read = _select([_row(1, cell=_code_review_cell([_round(after_merge=1)]))])
        assert read.prs[0].rounds_by_skill == {"code-review": []}
        assert read.prs[0].usd_by_skill == {"code-review": []}
        assert read.after_merge_rounds == {"code-review": 1}

    def test_an_added_integer_round_key_and_an_unknown_skill_key_pass_through(self):
        with_added_key = _round(**{"zz-added-count": 7})
        read = _select([_row(1, cell=_code_review_cell([with_added_key], **{"zz-unknown-skill": [_round()]}))])
        assert read.prs[0].rounds_by_skill["code-review"][0]["zz-added-count"] == 7
        assert len(read.prs[0].rounds_by_skill["zz-unknown-skill"]) == 1
        assert len(read.prs[0].usd_by_skill["zz-unknown-skill"]) == 1

    def test_a_cell_with_no_rounds_at_all_is_kept_with_no_skills(self):
        read = _select([_row(1, cell={"rule": review_rounds.REVIEW_ROUND_RULE, "rounds": {}})])
        assert (read.prs[0].rounds_by_skill, read.prs[0].usd_by_skill) == ({}, {})


def _account_root(tmp_path: Path, name: str) -> Path:
    """An account's projects directory whose parent name carries _LEDGER_DIR_MARKER, so a leaked path shows."""
    root = tmp_path / f"{name}-{_LEDGER_DIR_MARKER}" / "projects"
    root.mkdir(parents=True)
    return root


def _ledger_file(root: Path) -> Path:
    return root.parent / "pr-cost-ledger.tsv"


def _read(roots: list[Path]) -> ledger_rounds.LedgerRead:
    return ledger_rounds.read_ledger_prs(roots, _HOST, _REPO, subcommand=_SUBCOMMAND)


def _parse_error_message(ledger_text: str) -> str:
    with pytest.raises(pr_cost_ledger._PrCostLedgerParseError) as raised:
        pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_text)
    return str(raised.value)


def _assert_stderr_is_only(capsys, expected_stderr: str, *, leaked_fragments: tuple[str, ...]) -> None:
    out, err = capsys.readouterr()
    assert err == expected_stderr + "\n"
    for fragment in leaked_fragments:
        assert fragment not in out
        assert fragment not in err


class TestReadLedgerPrs:
    def test_two_roots_read_both_ledgers_without_either_opting_in(self, tmp_path):
        root_a, root_b = _account_root(tmp_path, "a"), _account_root(tmp_path, "b")
        pr_cost_ledger._write_pr_cost_ledger_file(_ledger_file(root_a), [_row(1, cell=_code_review_cell())])
        pr_cost_ledger._write_pr_cost_ledger_file(_ledger_file(root_b), [_row(1, cell=_code_review_cell())])
        read = _read([root_a, root_b])
        assert (read.prs, read.pr_count) == ([], 1)
        assert read.excluded[ledger_rounds.EXCLUDED_MULTI_ROW] == 1

    def test_a_missing_ledger_yields_no_rows(self, tmp_path):
        read = _read([_account_root(tmp_path, "a")])
        assert (read.prs, read.pr_count) == ([], 0)
        assert read.excluded == dict.fromkeys(ledger_rounds.EXCLUSION_REASONS, 0)

    def test_a_header_only_ledger_yields_no_rows(self, tmp_path):
        root = _account_root(tmp_path, "a")
        pr_cost_ledger._write_pr_cost_ledger_file(_ledger_file(root), [])
        read = _read([root])
        assert (read.prs, read.pr_count) == ([], 0)

    def test_a_ledger_under_the_pre_review_rounds_header_lands_under_the_no_cell_reason(self, tmp_path):
        root = _account_root(tmp_path, "a")
        _ledger_file(root).write_text(
            _PRE_REVIEW_ROUNDS_HEADER_LINE + "\n" + _pre_review_rounds_row_line(host=_HOST, repo=_REPO) + "\n"
        )
        read = _read([root])
        assert read.prs == []
        assert read.pr_count == 1
        assert read.excluded[ledger_rounds.EXCLUDED_NO_CELL] == 1

    def test_a_ledger_that_fails_to_parse_exits_one_with_the_parse_error_and_no_path(self, tmp_path, capsys):
        root = _account_root(tmp_path, "a")
        bad_text = "not-a-ledger-header\n"
        _ledger_file(root).write_text(bad_text)
        with pytest.raises(SystemExit) as raised:
            _read([root])
        assert raised.value.code == 1
        _assert_stderr_is_only(
            capsys, f"{_SUBCOMMAND}: account-1: {_parse_error_message(bad_text)}",
            leaked_fragments=(_LEDGER_DIR_MARKER, str(tmp_path)),
        )

    def test_a_zero_length_ledger_exits_one_with_the_parse_error(self, tmp_path, capsys):
        root = _account_root(tmp_path, "a")
        _ledger_file(root).write_text("")
        with pytest.raises(SystemExit) as raised:
            _read([root])
        assert raised.value.code == 1
        _assert_stderr_is_only(
            capsys, f"{_SUBCOMMAND}: account-1: {_parse_error_message('')}",
            leaked_fragments=(_LEDGER_DIR_MARKER, str(tmp_path)),
        )

    def test_a_second_accounts_failure_is_named_by_its_own_ordinal(self, tmp_path, capsys):
        root_a, root_b = _account_root(tmp_path, "a"), _account_root(tmp_path, "b")
        pr_cost_ledger._write_pr_cost_ledger_file(_ledger_file(root_a), [_row(1, cell=_code_review_cell())])
        _ledger_file(root_b).write_bytes(b"\xff\xfe\x00")
        with pytest.raises(SystemExit):
            _read([root_a, root_b])
        # Ordinals follow sorted resolved paths, and "a-..." sorts before "b-...".
        assert capsys.readouterr().err == f"{_SUBCOMMAND}: account-2: ledger file could not be read\n"

    def test_a_failure_is_named_by_scope_redaction_ordinals_not_by_declared_position(self, tmp_path, capsys):
        root_sorted_first = _account_root(tmp_path, "a")
        root_sorted_last = _account_root(tmp_path, "b")
        declared_roots = [root_sorted_last, root_sorted_first]
        pr_cost_ledger._write_pr_cost_ledger_file(_ledger_file(root_sorted_last), [_row(1, cell=_code_review_cell())])
        _ledger_file(root_sorted_first).write_bytes(b"\xff\xfe\x00")
        ordinal = scope._redaction_ordinals(declared_roots)[root_sorted_first.resolve()]
        assert ordinal != declared_roots.index(root_sorted_first) + 1
        with pytest.raises(SystemExit):
            _read(declared_roots)
        _assert_stderr_is_only(
            capsys, f"{_SUBCOMMAND}: account-{ordinal}: ledger file could not be read",
            leaked_fragments=(_LEDGER_DIR_MARKER, str(tmp_path)),
        )

    @pytest.mark.parametrize(
        ("column", "rule_fragment"),
        [
            pytest.param("host", "malformed host value", id="host"),
            pytest.param("repo", "malformed repo value", id="repo"),
            pytest.param("pr_number", "non-numeric pr_number", id="pr-number"),
            pytest.param("machine", "malformed machine label", id="machine"),
            pytest.param("merged_at", "malformed merged_at", id="merged-at"),
            pytest.param("captured_at", "malformed captured_at", id="captured-at"),
            pytest.param("rate_stamp", "malformed rate_stamp", id="rate-stamp"),
            pytest.param("join_confidence", "unknown join_confidence", id="join-confidence"),
            pytest.param("status", "unknown status", id="status"),
            pytest.param("cache_read_usd", "non-numeric cache_read_usd", id="float-column"),
            pytest.param("additions", "non-numeric additions", id="int-column"),
            pytest.param("tests_changed", "malformed tests_changed", id="bool-column"),
        ],
    )
    def test_a_cell_that_fails_to_parse_is_never_echoed(self, tmp_path, capsys, column, rule_fragment):
        """The sentinel is uppercase, so it fails the lowercase-only columns as well as the typed ones."""
        root = _account_root(tmp_path, "a")
        ledger_text = (
            pr_cost_ledger._PR_COST_LEDGER_HEADER_LINE + "\n" + _current_line_with_cell(column, _CELL_SENTINEL) + "\n"
        )
        _ledger_file(root).write_text(ledger_text)
        parse_error_message = _parse_error_message(ledger_text)
        assert parse_error_message.startswith(f"line 2: {rule_fragment}")
        with pytest.raises(SystemExit) as raised:
            _read([root])
        assert raised.value.code == 1
        _assert_stderr_is_only(
            capsys, f"{_SUBCOMMAND}: account-1: {parse_error_message}{_PARSE_ERROR_SUFFIX}",
            leaked_fragments=(_CELL_SENTINEL, _LEDGER_DIR_MARKER, str(tmp_path)),
        )

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_an_unreadable_ledger_exits_one_with_a_fixed_message(self, tmp_path, capsys):
        root = _account_root(tmp_path, "a")
        pr_cost_ledger._write_pr_cost_ledger_file(_ledger_file(root), [_row(1, cell=_code_review_cell())])
        _ledger_file(root).chmod(0o000)
        with pytest.raises(SystemExit) as raised:
            _read([root])
        assert raised.value.code == 1
        _assert_stderr_is_only(
            capsys, f"{_SUBCOMMAND}: account-1: ledger file could not be read",
            leaked_fragments=(_LEDGER_DIR_MARKER, str(tmp_path)),
        )

    def test_a_directory_where_the_ledger_belongs_exits_one_with_a_fixed_message(self, tmp_path, capsys):
        root = _account_root(tmp_path, "a")
        _ledger_file(root).mkdir()
        with pytest.raises(SystemExit) as raised:
            _read([root])
        assert raised.value.code == 1
        _assert_stderr_is_only(
            capsys, f"{_SUBCOMMAND}: account-1: ledger file could not be read",
            leaked_fragments=(_LEDGER_DIR_MARKER, str(tmp_path)),
        )

    def test_a_ledger_holding_invalid_utf8_exits_one_with_a_fixed_message(self, tmp_path, capsys):
        root = _account_root(tmp_path, "a")
        _ledger_file(root).write_bytes(b"\xff\xfe\x00invalid")
        with pytest.raises(SystemExit) as raised:
            _read([root])
        assert raised.value.code == 1
        _assert_stderr_is_only(
            capsys, f"{_SUBCOMMAND}: account-1: ledger file could not be read",
            leaked_fragments=(_LEDGER_DIR_MARKER, str(tmp_path)),
        )

    @pytest.mark.parametrize(
        "make_unreadable_ledger",
        [
            pytest.param(lambda ledger: ledger.write_text("not-a-ledger-header\n"), id="parse-error"),
            pytest.param(lambda ledger: ledger.write_bytes(b"\xff\xfe\x00invalid"), id="invalid-utf8"),
            pytest.param(lambda ledger: ledger.mkdir(), id="directory-in-place-of-ledger"),
        ],
    )
    def test_the_exit_carries_no_chained_exception_that_could_echo_a_path_or_cell(
        self, tmp_path, make_unreadable_ledger,
    ):
        """The exit follows the except block: SystemExit raised inside it would keep the parse or read error as
        __context__, and that error's text can carry the ledger path or a cell."""
        root = _account_root(tmp_path, "a")
        make_unreadable_ledger(_ledger_file(root))
        with pytest.raises(SystemExit) as raised:
            _read([root])
        assert raised.value.__context__ is None

    def test_two_roots_that_alias_one_directory_read_its_ledger_once(self, tmp_path):
        real_root = _account_root(tmp_path, "a")
        alias_account_dir = tmp_path / f"alias-{_LEDGER_DIR_MARKER}"
        alias_account_dir.symlink_to(real_root.parent, target_is_directory=True)
        pr_cost_ledger._write_pr_cost_ledger_file(_ledger_file(real_root), [_row(1, cell=_code_review_cell())])
        read = _read([real_root, alias_account_dir / "projects"])
        assert [pr.number for pr in read.prs] == [1]
        assert read.excluded == dict.fromkeys(ledger_rounds.EXCLUSION_REASONS, 0)

    def test_a_cell_less_row_under_a_newer_cell_carrying_row_of_one_machine_leaves_a_reported_pr(self, tmp_path):
        root = _account_root(tmp_path, "a")
        older_without_cell = _row(1, machine="ma", captured_at="2026-01-02T00:00:00Z")
        newer_with_cell = _row(1, machine="ma", cell=_code_review_cell(), captured_at="2026-01-03T00:00:00Z")
        pr_cost_ledger._write_pr_cost_ledger_file(_ledger_file(root), [older_without_cell, newer_with_cell])
        read = _read([root])
        assert [pr.number for pr in read.prs] == [1]
        assert read.excluded == dict.fromkeys(ledger_rounds.EXCLUSION_REASONS, 0)

    def test_a_newer_cell_less_correction_over_an_older_cell_carrying_row_of_one_machine_is_excluded_as_no_cell(
        self, tmp_path,
    ):
        root = _account_root(tmp_path, "a")
        older_with_cell = _row(1, machine="ma", cell=_code_review_cell(), captured_at="2026-01-02T00:00:00Z")
        newer_without_cell = _row(1, machine="ma", captured_at="2026-01-03T00:00:00Z")
        pr_cost_ledger._write_pr_cost_ledger_file(_ledger_file(root), [older_with_cell, newer_without_cell])
        read = _read([root])
        assert read.prs == []
        assert read.pr_count == 1
        assert read.excluded[ledger_rounds.EXCLUDED_NO_CELL] == 1


class TestReadLedgerPrsLedgerPathOverride:
    _OVERRIDE_SENTINEL = "zz-override-sentinel.tsv"

    def test_one_root_reads_the_override_file_in_place_of_its_own_ledger(self, tmp_path, monkeypatch):
        root = _account_root(tmp_path, "a")
        override_file = tmp_path / self._OVERRIDE_SENTINEL
        pr_cost_ledger._write_pr_cost_ledger_file(_ledger_file(root), [_row(1, cell=_code_review_cell())])
        pr_cost_ledger._write_pr_cost_ledger_file(override_file, [_row(2, cell=_code_review_cell())])
        monkeypatch.setenv(_LEDGER_PATH_ENV, str(override_file))
        assert [pr.number for pr in _read([root]).prs] == [2]

    def test_two_roots_exit_two_with_the_fixed_refusal(self, tmp_path, monkeypatch, capsys):
        roots = [_account_root(tmp_path, "a"), _account_root(tmp_path, "b")]
        monkeypatch.setenv(_LEDGER_PATH_ENV, str(tmp_path / self._OVERRIDE_SENTINEL))
        with pytest.raises(SystemExit) as raised:
            _read(roots)
        assert raised.value.code == 2
        _assert_stderr_is_only(
            capsys, _REFUSED_ACROSS_ROOTS_MESSAGE,
            leaked_fragments=(_LEDGER_DIR_MARKER, str(tmp_path), self._OVERRIDE_SENTINEL),
        )

    def test_an_empty_value_counts_as_unset_even_across_two_roots(self, tmp_path, monkeypatch):
        root_a, root_b = _account_root(tmp_path, "a"), _account_root(tmp_path, "b")
        pr_cost_ledger._write_pr_cost_ledger_file(_ledger_file(root_a), [_row(1, cell=_code_review_cell())])
        monkeypatch.setenv(_LEDGER_PATH_ENV, "")
        assert [pr.number for pr in _read([root_a, root_b]).prs] == [1]

    def test_a_relative_value_exits_one_naming_the_account_and_not_the_value(self, tmp_path, monkeypatch, capsys):
        relative_sentinel = f"zz-relative-{self._OVERRIDE_SENTINEL}"
        monkeypatch.setenv(_LEDGER_PATH_ENV, relative_sentinel)
        with pytest.raises(SystemExit) as raised:
            _read([_account_root(tmp_path, "a")])
        assert raised.value.code == 1
        _assert_stderr_is_only(
            capsys, f"{_SUBCOMMAND}: account-1: PR_COST_LEDGER_PATH must be an absolute path",
            leaked_fragments=(_LEDGER_DIR_MARKER, str(tmp_path), relative_sentinel),
        )


class TestWriterBuiltRoundRepricesToItsStoredDollars:
    def test_a_round_the_writer_built_reads_back_at_about_its_stored_micro_dollars(
        self, fake_projects, tmp_path,
    ):
        """The reader re-prices a round from its token splits and ignores the stored micro-dollars, so the splits must
        cover exactly the turns the writer priced. The first round holds a turn of every pricing variant, both
        cache-write classes and a dispatch, plus an unpriceable-model turn whose tokens must stay out of the splits.
        A later round before the merge sits in a session file that sorts first, so the kept order is the writer's time
        order. A third round opens after the merge, so the writer's after_merge flag must reach the reader."""
        branch = "feat"
        merged_at = "2026-09-01T00:00:00Z"
        _write_jsonl(fake_projects / "sess-writer.jsonl", [
            _priced_with_classes(
                "claude-sonnet-5", 100_000, branch=branch, ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review"), _agent_use("d1", "staff-sdet")],
            ),
            _priced_with_classes("claude-sonnet-5", 200_000, branch=branch, ts="2026-08-01T10:01:00.000Z", speed="fast"),
            _priced_with_classes(
                "claude-sonnet-5", 300_000, branch=branch, ts="2026-08-01T10:02:00.000Z", inference_geo="us",
            ),
            _priced_with_classes(
                "claude-sonnet-5", 400_000, branch=branch, ts="2026-08-01T10:03:00.000Z", speed="fast",
                inference_geo="us",
            ),
            _priced_with_classes(_UNPRICED_MODEL, 500_000, branch=branch, ts="2026-08-01T10:04:00.000Z"),
            _user_msg("thanks", branch=branch, ts="2026-08-01T10:05:00.000Z"),
        ])
        dispatched_turn = _priced_with_classes(
            "claude-sonnet-5", 600_000, branch=branch, ts="2026-08-01T10:00:30.000Z", speed="fast",
        )
        dispatched_turn["isSidechain"] = True
        _write_subagent_dispatch(fake_projects, "sess-writer", "agent-1", "d1", [dispatched_turn])
        _write_jsonl(fake_projects / "sess-a-later-before-merge.jsonl", [
            _priced_with_classes(
                "claude-sonnet-5", 700_000, branch=branch, ts="2026-08-15T09:00:00.000Z",
                content=[_skill_block("s2", "code-review")],
            ),
            _user_msg("thanks", branch=branch, ts="2026-08-15T09:05:00.000Z"),
        ])
        _write_jsonl(fake_projects / "sess-after-merge.jsonl", [
            _priced_with_classes(
                "claude-sonnet-5", 800_000, branch=branch, ts="2026-09-02T09:00:00.000Z",
                content=[_skill_block("s3", "code-review")],
            ),
            _user_msg("thanks", branch=branch, ts="2026-09-02T09:05:00.000Z"),
        ])
        written_rounds = pr_cost._scan_review_rounds_by_branch(_pr_cost_args(record=True), fake_projects.parent)[branch]
        cell, absence, _build_error = pr_cost._review_rounds_cell_outcome(written_rounds, None, merged_at)
        assert absence is None
        root = _account_root(tmp_path, "a")
        pr_cost_ledger._write_pr_cost_ledger_file(_ledger_file(root), [_row(1, cell=cell)])

        read = _read([root])

        (ledger_pr,) = read.prs
        read_rounds = ledger_pr.rounds_by_skill["code-review"]
        main_input_tokens = [each["main_tokens"]["claude-sonnet-5"]["standard"]["input"] for each in read_rounds]
        assert main_input_tokens == [100_001, 700_001]
        assert read.after_merge_rounds == {"code-review": 1}
        read_round = read_rounds[0]
        assert set(read_round["main_tokens"]) == {"claude-sonnet-5"}
        assert set(read_round["main_tokens"]["claude-sonnet-5"]) == set(pricing._PRICING_VARIANTS)
        assert set(read_round["agent_tokens"]["claude-sonnet-5"]) == {"fast"}
        assert read_round["unpriced_turns"] == 1
        stored_dollars = (read_round["main_usd_micros"] + read_round["agent_usd_micros"]) / 1_000_000
        # The writer rounds main and agent dollars to a micro-dollar each; the reader sums the unrounded prices.
        assert _round_usd(read_round) == pytest.approx(stored_dollars, abs=2e-6)
        assert ledger_pr.usd_by_skill["code-review"] == [_round_usd(each) for each in read_rounds]
