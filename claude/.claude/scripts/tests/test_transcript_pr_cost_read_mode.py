"""Tests for pr-cost read mode's per-skill round-count columns: pr_cost._review_round_counts and the
`<skill>-rounds` columns _print_pr_cost_ledger_rows appends. Split from test_transcript_pr_cost.py, which is past the
file-size limit."""
import re
import subprocess
from datetime import UTC, datetime

from transcript_analysis import ledger_rounds, pr_cost, pr_cost_ledger, review_rounds

from ._pr_cost_helpers import (
    _PRE_REVIEW_ROUNDS_HEADER_LINE,
    _fake_pr_cost_subprocess_run,
    _ledger_pr_row,
    _pr_cost_args,
    _pre_review_rounds_row_line,
    _priceable_round,
    _review_rounds_cell,
)
from .conftest import _two_declared_roots

_NOW = datetime(2026, 8, 10, tzinfo=UTC)
_HOST = "github.com"
_REPO = "owner/repo"
_ROUND_COUNT_HEADERS = [f"{skill}-rounds" for skill in review_rounds.REVIEW_SKILLS]
_ROUND_COUNT_FIELD_COUNT = len(review_rounds.REVIEW_SKILLS)
_LEDGER_FILENAME = "pr-cost-ledger.tsv"


def _round_without_after_merge_key() -> dict:
    return {key: value for key, value in _priceable_round().items() if key != "after_merge"}


def _cell_of_counts(*counts: int) -> dict:
    """A cell holding `counts[i]` rounds before merge under REVIEW_SKILLS[i], written in reversed skill order. The
    reversal protects only the tests that pass the dict straight to `_review_round_counts`, because a cell round-tripped
    through the ledger file is encoded with sorted keys and so already follows REVIEW_SKILLS order."""
    rounds_by_skill = {
        skill: [_priceable_round(after_merge=0) for _ in range(counts[review_rounds.REVIEW_SKILLS.index(skill)])]
        for skill in reversed(review_rounds.REVIEW_SKILLS)
    }
    return _review_rounds_cell(rounds_by_skill)


def _row(pr_number: int, cell: dict | None, *, repo: str = _REPO, **overrides) -> dict:
    return _ledger_pr_row(pr_number, host=_HOST, repo=repo, machine="ma", cell=cell, **overrides)


def _run_read_mode(fake_projects, monkeypatch, tmp_path, rows: list[dict], capsys) -> str:
    """Runs read mode over a one-account ledger holding `rows` and returns the report's stdout and stderr joined."""
    ledger_path = tmp_path / _LEDGER_FILENAME
    pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, rows)
    monkeypatch.setenv("PR_COST_LEDGER_PATH", str(ledger_path))
    monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())
    pr_cost._pr_cost_report(_pr_cost_args(), _NOW, [fake_projects.parent])
    captured = capsys.readouterr()
    return captured.out + captured.err


def _header_line(output: str) -> str:
    return next(line for line in output.splitlines() if line.startswith("Repo "))


def _row_lines(output: str, pr_number: int) -> list[str]:
    """The printed rows for `pr_number`, which sits in the second whitespace-separated field."""
    return [
        line for line in output.splitlines()
        if len(line.split()) > 1 and line.split()[1] == str(pr_number) and not line.startswith("Repo ")
    ]


def _round_count_fields(line: str) -> list[str]:
    return line.split()[-_ROUND_COUNT_FIELD_COUNT:]


def _right_edges(line: str) -> list[int]:
    return [match.end() for match in re.finditer(r"\S+", line)][-_ROUND_COUNT_FIELD_COUNT:]


class TestReviewRoundCounts:
    def test_a_row_with_no_cell_has_no_counts(self):
        assert pr_cost._review_round_counts(None) is None

    def test_counts_follow_review_skills_order_not_the_cells_key_order(self):
        assert pr_cost._review_round_counts(_cell_of_counts(1, 2, 3)) == (1, 2, 3)

    def test_a_round_flagged_after_merge_is_not_counted_and_a_round_with_no_flag_is(self):
        cell = _review_rounds_cell({
            "code-review": [_priceable_round(after_merge=1), _priceable_round(after_merge=0), _round_without_after_merge_key()],
        })

        assert pr_cost._review_round_counts(cell)[0] == 2

    def test_a_missing_skill_key_counts_zero_and_an_unknown_skill_key_changes_nothing(self):
        cell = _review_rounds_cell({
            "plan-review": [_priceable_round()],
            "unknown-skill": [_priceable_round(), _priceable_round()],
        })

        assert pr_cost._review_round_counts(cell) == (0, 1, 0)

    def test_a_cell_with_empty_lists_or_no_rounds_counts_zeros_not_none(self):
        empty_lists = _review_rounds_cell({skill: [] for skill in review_rounds.REVIEW_SKILLS})
        no_rounds = _review_rounds_cell({})

        assert pr_cost._review_round_counts(empty_lists) == (0, 0, 0)
        assert pr_cost._review_round_counts(no_rounds) == (0, 0, 0)

    def test_the_cells_rule_does_not_change_the_counts(self):
        cell = _cell_of_counts(1, 2, 3)
        cell["rule"] = review_rounds.REVIEW_ROUND_RULE + 1

        assert pr_cost._review_round_counts(cell) == (1, 2, 3)

    def test_counts_equal_the_kept_rounds_ledger_rounds_reports_for_the_same_row(self):
        cell = _review_rounds_cell({
            "code-review": [_priceable_round(after_merge=1), _priceable_round(after_merge=0), _round_without_after_merge_key()],
            "plan-review": [_priceable_round(after_merge=1), _priceable_round(after_merge=0), _round_without_after_merge_key(),
                            _priceable_round(after_merge=0)],
            "ready-for-review": [_priceable_round(after_merge=1), _priceable_round(after_merge=0),
                                 _round_without_after_merge_key(), _priceable_round(after_merge=0),
                                 _priceable_round(after_merge=0)],
        })
        row = _row(1, cell)

        ledger_pr = ledger_rounds.select_ledger_prs([[row]], _HOST, _REPO).prs[0]

        assert pr_cost._review_round_counts(cell) == tuple(
            len(ledger_pr.rounds_by_skill[skill]) for skill in review_rounds.REVIEW_SKILLS
        )
        assert len(set(pr_cost._review_round_counts(cell))) == len(review_rounds.REVIEW_SKILLS)


class TestReadModeRoundCountColumns:
    def test_header_ends_with_one_rounds_column_per_review_skill_in_order(self, fake_projects, monkeypatch, tmp_path, capsys):
        output = _run_read_mode(fake_projects, monkeypatch, tmp_path, [_row(1, _cell_of_counts(1, 2, 3))], capsys)

        header_fields = _header_line(output).split()

        assert header_fields[-_ROUND_COUNT_FIELD_COUNT:] == _ROUND_COUNT_HEADERS
        assert len(pr_cost._review_round_counts(_cell_of_counts(1, 2, 3))) == _ROUND_COUNT_FIELD_COUNT
        assert header_fields[-3:] == ["code-review-rounds", "plan-review-rounds", "ready-for-review-rounds"]

    def test_each_row_ends_with_its_own_counts_under_each_skills_header(self, fake_projects, monkeypatch, tmp_path, capsys):
        rows = [
            _row(10, _cell_of_counts(3, 0, 1), captured_at="2026-01-01T00:00:00Z"),
            _row(10, _cell_of_counts(0, 2, 2), captured_at="2026-01-02T00:00:00Z"),
            _row(20, _cell_of_counts(2, 2, 0), repo="other/repo"),
            _row(30, _cell_of_counts(2, 1, 1) | {"rule": review_rounds.REVIEW_ROUND_RULE + 1}),
            _row(40, _cell_of_counts(0, 0, 2), join_confidence=pr_cost_ledger._PR_COST_JOIN_CONFIDENCE_LOW),
            _row(50, _cell_of_counts(1, 2, 3)),
        ]

        ledger_view = ledger_rounds.select_ledger_prs([rows], _HOST, _REPO)
        assert ledger_view.excluded[ledger_rounds.EXCLUDED_RULE] == 1
        assert ledger_view.excluded[ledger_rounds.EXCLUDED_JOIN_CONFIDENCE_LOW] == 1
        assert 50 in {ledger_pr.number for ledger_pr in ledger_view.prs}

        output = _run_read_mode(fake_projects, monkeypatch, tmp_path, rows, capsys)

        superseded_line, current_line = _row_lines(output, 10)
        assert (_round_count_fields(superseded_line), _round_count_fields(current_line)) == (
            ["3", "0", "1"], ["0", "2", "2"],
        )
        assert _round_count_fields(_row_lines(output, 20)[0]) == ["2", "2", "0"]
        assert _round_count_fields(_row_lines(output, 30)[0]) == ["2", "1", "1"]
        assert _round_count_fields(_row_lines(output, 40)[0]) == ["0", "0", "2"]
        assert _round_count_fields(_row_lines(output, 50)[0]) == ["1", "2", "3"]

    def test_a_round_flagged_after_merge_is_not_printed_and_a_round_with_no_flag_is(
        self, fake_projects, monkeypatch, tmp_path, capsys,
    ):
        cell = _review_rounds_cell({
            "code-review": [_priceable_round(after_merge=1), _priceable_round(after_merge=0)],
            "plan-review": [_round_without_after_merge_key()],
            "ready-for-review": [_priceable_round(after_merge=1)],
        })

        output = _run_read_mode(fake_projects, monkeypatch, tmp_path, [_row(1, cell)], capsys)

        assert _round_count_fields(_row_lines(output, 1)[0]) == ["1", "1", "0"]

    def test_an_all_zero_cell_prints_zeros_and_a_cell_less_row_prints_dashes(
        self, fake_projects, monkeypatch, tmp_path, capsys,
    ):
        rows = [_row(1, _cell_of_counts(0, 0, 0)), _row(2, None)]

        output = _run_read_mode(fake_projects, monkeypatch, tmp_path, rows, capsys)

        assert _round_count_fields(_row_lines(output, 1)[0]) == ["0", "0", "0"]
        assert _round_count_fields(_row_lines(output, 2)[0]) == ["-", "-", "-"]

    def test_a_refilled_pr_prints_its_older_cell_less_row_as_dashes_beside_its_new_counted_row(
        self, fake_projects, monkeypatch, tmp_path, capsys,
    ):
        rows = [
            _row(10, None, captured_at="2026-01-01T00:00:00Z"),
            _row(10, _cell_of_counts(2, 1, 0), captured_at="2026-01-02T00:00:00Z"),
        ]

        output = _run_read_mode(fake_projects, monkeypatch, tmp_path, rows, capsys)

        older_line, newer_line = _row_lines(output, 10)
        assert (_round_count_fields(older_line), _round_count_fields(newer_line)) == (
            ["-", "-", "-"], ["2", "1", "0"],
        )

    def test_each_count_is_right_aligned_under_its_header(self, fake_projects, monkeypatch, tmp_path, capsys):
        output = _run_read_mode(fake_projects, monkeypatch, tmp_path, [_row(1, _cell_of_counts(1, 2, 3))], capsys)

        assert _right_edges(_row_lines(output, 1)[0]) == _right_edges(_header_line(output))

    def test_a_second_account_under_the_pre_review_rounds_header_prints_dashes_for_each_of_its_rows(
        self, tmp_path, monkeypatch, capsys,
    ):
        roots = _two_declared_roots(tmp_path, monkeypatch)
        ledger_paths = [pr_cost_ledger._pr_cost_ledger_path(config_dir_override=root.parent) for root in roots]
        pr_cost_ledger._write_pr_cost_ledger_file(ledger_paths[0], [_row(101, _cell_of_counts(1, 2, 3))])
        ledger_paths[1].write_text(
            _PRE_REVIEW_ROUNDS_HEADER_LINE + "\n" + _pre_review_rounds_row_line(pr_number=202) + "\n"
        )
        monkeypatch.setattr(subprocess, "run", _fake_pr_cost_subprocess_run())

        pr_cost._pr_cost_report(_pr_cost_args(all_accounts=True), _NOW, roots)

        output = capsys.readouterr().out
        assert _round_count_fields(_row_lines(output, 101)[0]) == ["1", "2", "3"]
        assert _round_count_fields(_row_lines(output, 202)[0]) == ["-", "-", "-"]


class TestReadModeRoundCountWithholding:
    def test_a_populated_cell_adds_only_its_three_counts_to_the_output(
        self, fake_projects, monkeypatch, tmp_path, capsys,
    ):
        unknown_skill_key = "zz-unknown-skill-key-zz"
        model_sentinel = "zz-model-sentinel-zz"
        distinctive_rule = 7_654_321_987
        distinctive_micros = 987_654_321
        distinctive_tokens = 876_543_210
        rate_stamp_sentinel = "2031-03-04"
        cell = _cell_of_counts(1, 2, 3)
        cell["rule"] = distinctive_rule
        cell["rounds"][unknown_skill_key] = [_priceable_round()]
        cell["rounds"]["code-review"][0].update(
            main_usd_micros=distinctive_micros,
            main_tokens={model_sentinel: {"standard": {"input": distinctive_tokens}}},
        )
        row = _row(7, cell, rate_stamp=rate_stamp_sentinel)

        output = _run_read_mode(fake_projects, monkeypatch, tmp_path, [row], capsys)

        printed_row = _row_lines(output, 7)
        assert len(printed_row) == 1
        assert len(printed_row[0].split()) == 6 + _ROUND_COUNT_FIELD_COUNT
        assert _round_count_fields(printed_row[0]) == ["1", "2", "3"]
        for withheld in (
            unknown_skill_key, model_sentinel, str(distinctive_rule), str(distinctive_micros),
            str(distinctive_tokens), rate_stamp_sentinel,
        ):
            assert withheld not in output, withheld
