"""Tests for transcript_analysis/review_cost_by_size.py (review-cost-by-size).

Aggregates and the PR join are tested over literal records and round dicts. The scan is tested on
transcript files, and the command with `gh` faked.
"""
import math
import os
import re
import subprocess
import time
from collections import Counter

import pytest
from helpers import REPO_ROOT, heading_texts, normalize_heading
from transcript_analysis import gh_cli, review_cost_by_size, scope

from ._pr_cost_helpers import _fake_pr_cost_subprocess_run, _merged_pr
from .conftest import (
    _agent_use,
    _priced,
    _skill_block,
    _two_declared_roots,
    _user_msg,
    _write_jsonl,
    _write_subagent_dispatch,
)

# Module-level so test_select_tests.py's TestCrossDomainReadCompleteness sees this read.
_PRIVATE_PROJECT_REDACTION_DOC = REPO_ROOT / "docs" / "private-project-redaction.md"

_PROJECT_SLUG = "-home-user-testrepo"
_HEADER_ACTIVE_ACCOUNT = "REVIEW COST BY SIZE SOURCES (this repo (1 project dirs); active account only)"
_HEADER_EVERY_ACCOUNT = "REVIEW COST BY SIZE SOURCES (this repo (1 project dirs); every declared account)"
# A claude-sonnet-5 turn of 100_000 input tokens prices at this many dollars.
_ROUND_OPENING_TURN_DOLLARS = 0.20


def _review_cost_by_size_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    all_accounts: bool = False,
    this_repo_slugs: list[str] | None = None,
) -> object:
    """this_repo_slugs, when given, pre-seeds args._this_repo_slugs -- the cache
    _resolve_project_scope reads first -- so a direct cmd call under --this-repo never shells out to git."""
    attrs = {"projects": projects, "this_repo": this_repo, "all_accounts": all_accounts, "config_dir": None}
    if this_repo_slugs is not None:
        attrs["_this_repo_slugs"] = this_repo_slugs
    return type("A", (), attrs)()


def _merged_pr_sized(number: int, branch: str, *, additions: int, deletions: int) -> dict:
    """A merged-PR dict with explicit, unequal additions and deletions, since a line total of
    additions + deletions cannot tell the two apart when they are equal."""
    return {**_merged_pr(number, branch), "additions": additions, "deletions": deletions}


def _round_entry(
    branch: str, *, main_dollars: float, agent_dollars: float = 0.0, sort_ts: float,
    unpriced_turns: int = 0, dangling: int = 0,
) -> dict:
    """A round dict as compute_review_round_costs returns it, with only the fields the join reads."""
    return {
        "branch_key": (None, branch), "skill": "code-review", "ts": None,
        "main_dollars": main_dollars, "agent_dollars": agent_dollars, "agents": 0,
        "unpriced_turns": unpriced_turns, "dangling": dangling, "sort_key": (sort_ts, "session.jsonl", 0),
    }


def _record(number: int, lines: int, *round_dollars: float) -> review_cost_by_size._PrRecord:
    return review_cost_by_size._PrRecord(number=number, lines=lines, round_dollars=tuple(round_dollars))


def _write_code_review_session(session_path, branch: str, round_count: int) -> None:
    """One session of `round_count` code-review rounds on `branch`, each priced at
    _ROUND_OPENING_TURN_DOLLARS and closed by a fresh user prompt."""
    records = []
    for round_index in range(round_count):
        minute = 2 * round_index
        records.append(_priced(
            "claude-sonnet-5", input=100_000, branch=branch, ts=f"2026-08-01T10:{minute:02d}:00.000Z",
            content=[_skill_block(f"skill-{round_index}", "code-review")],
        ))
        records.append(_user_msg("thanks", branch=branch, ts=f"2026-08-01T10:{minute + 1:02d}:00.000Z"))
    _write_jsonl(session_path, records)


def _run_cmd(monkeypatch, capsys, args, *, merged_prs=(), call_log=None, **fake_kwargs):
    monkeypatch.setattr(
        subprocess, "run",
        _fake_pr_cost_subprocess_run(merged_prs=list(merged_prs), call_log=call_log, **fake_kwargs),
    )
    review_cost_by_size.cmd_review_cost_by_size(args)
    return capsys.readouterr()


def _printed_pr_numbers(stdout: str) -> list[int]:
    _, _, rows_section = stdout.partition("Per-PR rows")
    return [int(match.group(1)) for match in re.finditer(r"^\s+(\d+)\s+[\d,]+\s+\d+\s", rows_section, re.MULTILINE)]


class TestComputeAggregates:
    def test_each_size_bucket_holds_two_prs_and_reports_the_midpoint_medians(self):
        """Lines 0, 149, 150, 399, 400, 799, 800 and 10000 put two PRs in each half-open bucket.
        Each bucket's two PRs differ on every measure, so the median is the midpoint, which neither
        median_low nor median_high returns."""
        records = [
            _record(1, 0, 1.0),
            _record(2, 149, 2.0, 4.0),
            _record(3, 150, 1.0, 1.0, 1.0),
            _record(4, 399, 2.0, 2.0, 2.0, 2.0, 2.0),
            _record(5, 400, 4.0, 4.0),
            _record(6, 799, 1.0),
            _record(7, 800, 6.0),
            _record(8, 10000, 8.0, 4.0, 4.0, 4.0),
        ]
        buckets = review_cost_by_size._compute_aggregates(records)["buckets"]
        assert [(bucket["lower"], bucket["upper"], bucket["pr_count"]) for bucket in buckets] == [
            (0, 150, 2), (150, 400, 2), (400, 800, 2), (800, None, 2),
        ]
        assert [bucket["medians"] for bucket in buckets] == [
            {"round_count": 1.5, "total_dollars": 3.5, "mean_dollars": 2.0},
            {"round_count": 4.0, "total_dollars": 6.5, "mean_dollars": 1.5},
            {"round_count": 1.5, "total_dollars": 4.5, "mean_dollars": 2.5},
            {"round_count": 2.5, "total_dollars": 13.0, "mean_dollars": 5.5},
        ]

    def test_tied_series_use_average_ranks_and_each_measure_has_its_own_rho(self):
        """Lines 10, 20, 20, 30, 40 tie two PRs. Round counts 2, 1, 3, 3, 1, totals 4, 5, 3, 6, 3 and means
        2, 5, 1, 2, 3 each order the PRs differently. Average ranks give line ranks 1, 2.5, 2.5, 4, 5.
        The rhos below are Pearson's r over those and each measure's average ranks, worked by hand. Min ranks,
        ordinal ranks and raw Pearson each give a different value for every measure."""
        records = [
            _record(1, 10, 2.0, 2.0),
            _record(2, 20, 5.0),
            _record(3, 20, 1.0, 1.0, 1.0),
            _record(4, 30, 1.0, 2.0, 3.0),
            _record(5, 40, 3.0),
        ]
        spearman = review_cost_by_size._compute_aggregates(records)["spearman"]
        assert spearman["round_count"] == (pytest.approx(-1.5 / math.sqrt(85.5)), 5)
        assert spearman["total_dollars"] == (pytest.approx(-0.75 / 9.5), 5)
        assert spearman["mean_dollars"] == (pytest.approx(2.5 / 9.5), 5)

    def test_elasticities_are_half_one_and_a_half_one_for_the_three_measures(self):
        """PRs of 1, 4, 9 and 16 lines with 1, 2, 3 and 4 rounds, each round costing the PR's line count:
        rounds scale as lines^0.5, total dollars as lines^1.5 and mean dollars as lines^1."""
        records = [_record(number, lines, *([float(lines)] * rounds))
                   for number, (lines, rounds) in enumerate([(1, 1), (4, 2), (9, 3), (16, 4)], start=1)]
        elasticity = review_cost_by_size._compute_aggregates(records)["elasticity"]
        assert elasticity["round_count"][0] == pytest.approx(0.5)
        assert elasticity["total_dollars"][0] == pytest.approx(1.5)
        assert elasticity["mean_dollars"][0] == pytest.approx(1.0)
        assert [elasticity[measure][1] for measure in review_cost_by_size._MEASURES] == [4, 4, 4]

    def test_zero_line_pr_drops_out_of_every_elasticity(self):
        records = [_record(number, lines, *([float(lines)] * rounds))
                   for number, (lines, rounds) in enumerate([(1, 1), (4, 2), (9, 3), (16, 4)], start=1)]
        records.append(_record(5, 0, 3.0, 3.0))
        aggregates = review_cost_by_size._compute_aggregates(records)
        assert [aggregates["elasticity"][measure][1] for measure in review_cost_by_size._MEASURES] == [4, 4, 4]
        assert [aggregates["spearman"][measure][1] for measure in review_cost_by_size._MEASURES] == [5, 5, 5]
        assert aggregates["elasticity"]["total_dollars"][0] == pytest.approx(1.5)

    def test_zero_dollar_pr_drops_out_of_the_two_dollar_elasticities_only(self):
        """A 25-line PR with five zero-dollar rounds sits on the rounds line (ln 5 / ln 25 = 0.5), so the
        rounds slope is unchanged while the rounds count rises to 5 and the dollar counts stay at 4."""
        records = [_record(number, lines, *([float(lines)] * rounds))
                   for number, (lines, rounds) in enumerate([(1, 1), (4, 2), (9, 3), (16, 4)], start=1)]
        records.append(_record(5, 25, *([0.0] * 5)))
        aggregates = review_cost_by_size._compute_aggregates(records)
        elasticity = aggregates["elasticity"]
        assert elasticity["round_count"] == (pytest.approx(0.5), 5)
        assert elasticity["total_dollars"] == (pytest.approx(1.5), 4)
        assert elasticity["mean_dollars"] == (pytest.approx(1.0), 4)
        assert [aggregates["spearman"][measure][1] for measure in review_cost_by_size._MEASURES] == [5, 5, 5]

    def test_constant_dollars_among_the_surviving_prs_give_slope_zero_despite_a_zero_dollar_pr(self):
        """The constant-value check runs on the pairs that survive the positivity filter. Run on the unfiltered
        values, the zero-dollar PR would make them unequal and the regression would return a tiny nonzero
        slope for these sizes (about -7e-32 after the logs)."""
        constant_dollars = 5.901146528953873
        records = [_record(number, lines, constant_dollars)
                   for number, lines in enumerate([1847, 147, 3255], start=1)]
        records.append(_record(4, 25, 0.0))
        elasticity = review_cost_by_size._compute_aggregates(records)["elasticity"]
        assert elasticity["total_dollars"] == (0.0, 3)

    def test_identical_line_counts_among_the_surviving_prs_give_no_slope_despite_a_zero_line_pr(self):
        """The same-size check runs on the pairs that survive the positivity filter. Run on the unfiltered
        line counts, the zero-line PR would hide the five equal sizes, and the constant mean-dollar series
        would return 0.0 instead of None."""
        records = [_record(number, 7, *([1.0] * number)) for number in range(1, 6)]
        records.append(_record(6, 0, 1.0))
        elasticity = review_cost_by_size._compute_aggregates(records)["elasticity"]
        assert elasticity == {measure: (None, 5) for measure in review_cost_by_size._MEASURES}

    def test_constant_round_count_gives_slope_zero_and_no_rho(self):
        """A constant series has slope 0.0 by this module's definition (_log_log_slope), not by the regression
        library; statistics.correlation raises on it, so the rho is None."""
        records = [_record(1, 10, 1.0), _record(2, 100, 2.0), _record(3, 1000, 3.0)]
        aggregates = review_cost_by_size._compute_aggregates(records)
        assert aggregates["elasticity"]["round_count"] == (0.0, 3)
        assert aggregates["spearman"]["round_count"] == (None, 3)

    def test_constant_round_count_above_one_gives_exactly_positive_zero(self):
        """Three 6-round PRs: the regression's slope on a constant y can come out as a tiny nonzero (even
        negative) float, which would print as -0.00. A constant value is exactly 0.0."""
        records = [_record(number, lines, *([1.0] * 6)) for number, lines in enumerate([7, 130, 2900], start=1)]
        slope, pr_count = review_cost_by_size._compute_aggregates(records)["elasticity"]["round_count"]
        assert (slope, pr_count) == (0.0, 3)
        assert math.copysign(1.0, slope) > 0

    def test_distinct_line_counts_whose_logs_are_equal_give_no_slope(self):
        """ln(10**16) and ln(10**16 + 1) are the same float, so the logged x series is constant although the
        raw line counts differ. The pre-log check passes and statistics.linear_regression raises, which the
        slope turns into None."""
        assert review_cost_by_size._log_log_slope([10**16, 10**16 + 1], [1.0, 2.0]) == (None, 2)

    def test_identical_line_counts_give_no_slope_even_when_values_vary(self):
        """Five PRs of the same size have a constant x. The slope is undefined however the logs sum, and each
        measure still reports the five PRs it saw."""
        records = [_record(number, 7, float(number)) for number in range(1, 6)]
        elasticity = review_cost_by_size._compute_aggregates(records)["elasticity"]
        assert elasticity == {measure: (None, 5) for measure in review_cost_by_size._MEASURES}

    def test_identical_line_counts_with_identical_values_give_no_slope_rather_than_zero(self):
        """When both series are constant the slope is undefined, not 0.0: the equal-sizes check takes
        precedence over the equal-values check."""
        assert review_cost_by_size._log_log_slope([7, 7, 7], [2.0, 2.0, 2.0]) == (None, 3)

    def test_within_pr_trend_counts_only_prs_with_four_rounds_and_a_defined_rho(self):
        """Four 4-round PRs have rhos 1.0, -1.0, 0.8 and 0.0. A 3-round falling PR is under the round
        floor and a 4-round constant PR has no rho, so neither counts."""
        records = [
            _record(1, 10, 1.0, 2.0, 3.0, 4.0),
            _record(2, 20, 4.0, 3.0, 2.0, 1.0),
            _record(3, 30, 1.0, 3.0, 2.0, 4.0),
            _record(4, 40, 3.0, 1.0, 4.0, 2.0),
            _record(5, 50, 3.0, 2.0, 1.0),
            _record(6, 60, 2.0, 2.0, 2.0, 2.0),
        ]
        trend = review_cost_by_size._compute_aggregates(records)["trend"]
        assert trend["pr_count"] == 4
        assert trend["median_rho"] == pytest.approx(0.4)
        assert trend["share_positive"] == pytest.approx(0.5)

    def test_trend_is_undefined_when_every_pr_has_fewer_than_four_rounds(self):
        records = [_record(1, 10, 1.0, 2.0, 3.0), _record(2, 20, 5.0)]
        trend = review_cost_by_size._compute_aggregates(records)["trend"]
        assert trend == {"pr_count": 0, "median_rho": None, "share_positive": None}

    def test_empty_corpus_gives_none_for_every_value_and_zero_for_every_count(self):
        aggregates = review_cost_by_size._compute_aggregates([])
        assert aggregates["spearman"] == {measure: (None, 0) for measure in review_cost_by_size._MEASURES}
        assert aggregates["elasticity"] == {measure: (None, 0) for measure in review_cost_by_size._MEASURES}
        assert all(
            bucket["pr_count"] == 0 and set(bucket["medians"].values()) == {None} for bucket in aggregates["buckets"]
        )
        assert aggregates["trend"] == {"pr_count": 0, "median_rho": None, "share_positive": None}

    def test_one_pr_corpus_has_no_rho_slope_or_trend_and_its_bucket_reports_its_own_values(self):
        aggregates = review_cost_by_size._compute_aggregates([_record(1, 150, 1.0, 3.0)])
        assert aggregates["spearman"] == {measure: (None, 1) for measure in review_cost_by_size._MEASURES}
        assert aggregates["elasticity"] == {measure: (None, 1) for measure in review_cost_by_size._MEASURES}
        assert aggregates["trend"]["pr_count"] == 0
        populated = next(bucket for bucket in aggregates["buckets"] if bucket["lower"] == 150)
        assert populated["pr_count"] == 1
        assert populated["medians"] == {"round_count": 2, "total_dollars": 4.0, "mean_dollars": 2.0}
        assert [(bucket["pr_count"], set(bucket["medians"].values())) for bucket in aggregates["buckets"]
                if bucket["lower"] != 150] == [(0, {None})] * 3


class TestPrintedReport:
    """The stdout lines _print_aggregates and _print_join_diagnostics render from hand-computed fixtures."""

    @staticmethod
    def _aggregate_lines(capsys, records) -> list[str]:
        review_cost_by_size._print_aggregates(review_cost_by_size._compute_aggregates(records))
        return capsys.readouterr().out.splitlines()

    @staticmethod
    def _block_after(lines: list[str], header: str) -> list[str]:
        start = lines.index(header) + 1
        end = start
        while end < len(lines) and lines[end].startswith("  "):
            end += 1
        return lines[start:end]

    @classmethod
    def _spearman_block(cls, lines: list[str]) -> list[str]:
        return cls._block_after(lines, "Spearman rho of lines against (rho; PRs used):")

    @classmethod
    def _elasticity_block(cls, lines: list[str]) -> list[str]:
        return cls._block_after(lines, "Log-log elasticity against lines (slope; PRs used):")

    def test_elasticity_lines_show_slope_and_pr_count_per_measure(self, capsys):
        records = [_record(number, lines, *([float(lines)] * rounds))
                   for number, (lines, rounds) in enumerate([(1, 1), (4, 2), (9, 3), (16, 4)], start=1)]
        lines = self._aggregate_lines(capsys, records)
        assert "  rounds per PR: 0.50 (n=4)" in lines
        assert "  total dollars per PR: 1.50 (n=4)" in lines
        assert "  mean dollars per round: 1.00 (n=4)" in lines

    def test_spearman_lines_count_every_pr_while_elasticity_lines_drop_the_zero_line_pr(self, capsys):
        """The zero-line PR is ranked by Spearman but has no log, so each Spearman line reads n=5 and each
        elasticity line n=4; a Spearman count copied from the elasticity pairs fails."""
        records = [_record(number, lines, *([float(lines)] * rounds))
                   for number, (lines, rounds) in enumerate([(1, 1), (4, 2), (9, 3), (16, 4)], start=1)]
        records.append(_record(5, 0, 3.0, 3.0))
        lines = self._aggregate_lines(capsys, records)
        assert self._spearman_block(lines) == [
            "  rounds per PR: 0.82 (n=5)",
            "  total dollars per PR: 0.90 (n=5)",
            "  mean dollars per round: 0.90 (n=5)",
        ]
        assert self._elasticity_block(lines) == [
            "  rounds per PR: 0.50 (n=4)",
            "  total dollars per PR: 1.50 (n=4)",
            "  mean dollars per round: 1.00 (n=4)",
        ]

    def test_constant_round_count_prints_slope_zero_beside_an_undefined_rho(self, capsys):
        records = [_record(1, 10, 1.0), _record(2, 100, 2.0), _record(3, 1000, 3.0)]
        lines = self._aggregate_lines(capsys, records)
        assert "  rounds per PR: n/a (n=3)" in self._spearman_block(lines)
        assert "  rounds per PR: 0.00 (n=3)" in self._elasticity_block(lines)

    def test_empty_corpus_prints_every_spearman_line_as_undefined_over_zero_prs(self, capsys):
        lines = self._aggregate_lines(capsys, [])
        assert self._spearman_block(lines) == [
            "  rounds per PR: n/a (n=0)",
            "  total dollars per PR: n/a (n=0)",
            "  mean dollars per round: n/a (n=0)",
        ]

    def test_populated_bucket_line_shows_its_label_dollar_medians_and_one_decimal_rounds_median(self, capsys):
        """Rounds medians 1 and 2 give 1.5, total medians 1.00 and 6.00 give $3.50, mean medians 1.00 and
        3.00 give $2.00. The empty 800+ bucket prints n=0 and n/a."""
        records = [_record(1, 10, 1.0), _record(2, 20, 2.0, 4.0)]
        lines = self._aggregate_lines(capsys, records)
        assert (
            "  0-149 lines: n=2; rounds per PR 1.5; total dollars per PR $3.50; mean dollars per round $2.00"
        ) in lines
        assert (
            "  800+ lines: n=0; rounds per PR n/a; total dollars per PR n/a; mean dollars per round n/a"
        ) in lines

    def test_trend_line_prints_the_share_above_zero_as_a_one_decimal_percentage(self, capsys):
        records = [
            _record(1, 10, 1.0, 2.0, 3.0, 4.0),
            _record(2, 20, 4.0, 3.0, 2.0, 1.0),
            _record(3, 30, 1.0, 3.0, 2.0, 4.0),
            _record(4, 40, 3.0, 1.0, 4.0, 2.0),
        ]
        lines = self._aggregate_lines(capsys, records)
        assert "  PRs with a rho: 4; median rho 0.40; share above zero 50.0%" in lines

    def test_join_diagnostics_print_each_count_under_its_own_label(self, capsys):
        counts = review_cost_by_size._JoinCounts(
            merged_pr_count=11, unmatched_branch_count=22, ambiguous_branch_count=33,
            unpriced_turns=44, dangling_dispatches=55,
        )
        review_cost_by_size._print_join_diagnostics(66, counts, 77)
        assert capsys.readouterr().out.splitlines() == [
            "",
            "Join diagnostics",
            "  Merged PRs listed: 11",
            "  PRs joined: 66",
            "  Unmatched branches: 22",
            "  Ambiguous branches (excluded): 33",
            "  Unpriced turns inside joined rounds: 44",
            "  Dangling dispatches inside joined rounds: 55",
            "  Unreadable sources skipped: 77",
        ]


class TestJoinRoundsToPrs:
    def test_every_join_outcome(self):
        rounds = [
            # Arrive out of sort_key order: sorted order is the second, third, first.
            _round_entry("feat-joined", main_dollars=1.0, agent_dollars=0.5, sort_ts=3.0, unpriced_turns=1, dangling=0),
            _round_entry("feat-joined", main_dollars=2.0, agent_dollars=0.25, sort_ts=1.0, unpriced_turns=2, dangling=1),
            _round_entry("feat-joined", main_dollars=4.0, agent_dollars=0.0, sort_ts=2.0, unpriced_turns=0, dangling=3),
            _round_entry("feat-unmerged", main_dollars=9.0, sort_ts=1.0, unpriced_turns=5, dangling=7),
            _round_entry("feat-reused", main_dollars=1.0, sort_ts=1.0, unpriced_turns=11, dangling=13),
            _round_entry("main", main_dollars=1.0, sort_ts=1.0, unpriced_turns=17, dangling=19),
            _round_entry("worktree-agent-abc", main_dollars=1.0, sort_ts=1.0, unpriced_turns=23, dangling=29),
        ]
        merged_prs = [
            _merged_pr_sized(101, "feat-joined", additions=100, deletions=49),
            _merged_pr_sized(201, "feat-reused", additions=1, deletions=2),
            _merged_pr_sized(202, "feat-reused", additions=3, deletions=4),
            _merged_pr_sized(301, "feat-no-rounds", additions=5, deletions=6),
            _merged_pr_sized(401, "main", additions=7, deletions=8),
            _merged_pr_sized(402, "worktree-agent-abc", additions=9, deletions=10),
            # A second PR per never-join branch makes the never-join check observable ahead of the ambiguity check.
            _merged_pr_sized(403, "main", additions=11, deletions=12),
            _merged_pr_sized(404, "worktree-agent-abc", additions=13, deletions=14),
        ]
        records, counts = review_cost_by_size._join_rounds_to_prs(rounds, merged_prs)

        assert len(records) == 1
        record = records[0]
        assert (record.number, record.lines) == (101, 149)
        assert record.round_dollars == (2.25, 4.0, 1.5)
        assert record.round_count == 3
        assert record.total_dollars == pytest.approx(7.75)
        assert record.mean_dollars == pytest.approx(7.75 / 3)
        assert counts == review_cost_by_size._JoinCounts(
            merged_pr_count=8,
            unmatched_branch_count=3,  # feat-unmerged, main, worktree-agent-abc
            ambiguous_branch_count=1,  # feat-reused
            unpriced_turns=3,
            dangling_dispatches=4,
        )

    def test_a_multi_round_unmatched_or_ambiguous_branch_counts_once_and_adds_no_unpriced_or_dangling(self):
        rounds = [
            _round_entry("feat-joined", main_dollars=1.0, sort_ts=1.0, unpriced_turns=1, dangling=2),
            _round_entry("feat-unmerged", main_dollars=1.0, sort_ts=1.0, unpriced_turns=5, dangling=13),
            _round_entry("feat-unmerged", main_dollars=1.0, sort_ts=2.0, unpriced_turns=7, dangling=17),
            _round_entry("feat-unmerged", main_dollars=1.0, sort_ts=3.0, unpriced_turns=11, dangling=19),
            _round_entry("feat-reused", main_dollars=1.0, sort_ts=1.0, unpriced_turns=23, dangling=31),
            _round_entry("feat-reused", main_dollars=1.0, sort_ts=2.0, unpriced_turns=29, dangling=37),
            _round_entry("main", main_dollars=1.0, sort_ts=1.0, unpriced_turns=41, dangling=43),
            _round_entry("main", main_dollars=1.0, sort_ts=2.0, unpriced_turns=47, dangling=53),
            _round_entry("worktree-agent-abc", main_dollars=1.0, sort_ts=1.0, unpriced_turns=59, dangling=61),
            _round_entry("worktree-agent-abc", main_dollars=1.0, sort_ts=2.0, unpriced_turns=67, dangling=71),
        ]
        merged_prs = [
            _merged_pr_sized(101, "feat-joined", additions=1, deletions=1),
            _merged_pr_sized(201, "feat-reused", additions=1, deletions=1),
            _merged_pr_sized(202, "feat-reused", additions=1, deletions=1),
        ]
        records, counts = review_cost_by_size._join_rounds_to_prs(rounds, merged_prs)

        assert [record.number for record in records] == [101]
        assert counts == review_cost_by_size._JoinCounts(
            merged_pr_count=3,
            unmatched_branch_count=3,  # feat-unmerged, main, worktree-agent-abc
            ambiguous_branch_count=1,
            unpriced_turns=1,
            dangling_dispatches=2,
        )

    def test_a_branch_joins_only_the_pr_whose_head_name_equals_it_exactly(self):
        """Merged PRs headed `feat-2`, `feat/x` and `Feat` share a prefix, a path segment or a case-fold
        with the round branch `feat`, and none of them joins it. The round branch `feat-2-fix` extends the
        head `feat-2` and does not join it either."""
        records, counts = review_cost_by_size._join_rounds_to_prs(
            [
                _round_entry("feat", main_dollars=1.0, sort_ts=1.0),
                _round_entry("feat-2-fix", main_dollars=1.0, sort_ts=2.0),
            ],
            [
                _merged_pr_sized(1, "feat-2", additions=1, deletions=1),
                _merged_pr_sized(2, "feat/x", additions=1, deletions=1),
                _merged_pr_sized(3, "Feat", additions=1, deletions=1),
            ],
        )
        assert records == []
        assert (counts.unmatched_branch_count, counts.ambiguous_branch_count) == (2, 0)

    @pytest.mark.parametrize("default_branch", ["main", "master", "develop"])
    def test_a_default_branch_round_stays_unmatched_beside_a_merged_pr_headed_by_that_name(self, default_branch):
        records, counts = review_cost_by_size._join_rounds_to_prs(
            [_round_entry(default_branch, main_dollars=1.0, sort_ts=1.0)],
            [_merged_pr_sized(1, default_branch, additions=1, deletions=1)],
        )
        assert records == []
        assert (counts.unmatched_branch_count, counts.ambiguous_branch_count) == (1, 0)

    def test_a_detached_head_round_stays_unmatched_beside_a_merged_pr_headed_head(self):
        records, counts = review_cost_by_size._join_rounds_to_prs(
            [_round_entry(scope._DETACHED_HEAD_BRANCH, main_dollars=1.0, sort_ts=1.0)],
            [_merged_pr_sized(1, scope._DETACHED_HEAD_BRANCH, additions=1, deletions=1)],
        )
        assert records == []
        assert (counts.unmatched_branch_count, counts.ambiguous_branch_count) == (1, 0)

    @pytest.mark.parametrize("missing_field", ["additions", "deletions", "number", "headRefName"])
    def test_a_malformed_merged_pr_stops_the_join_instead_of_defaulting_to_zero_lines(self, missing_field):
        merged_pr = _merged_pr_sized(101, "feat-joined", additions=100, deletions=49)
        del merged_pr[missing_field]
        with pytest.raises(KeyError, match=missing_field):
            review_cost_by_size._join_rounds_to_prs(
                [_round_entry("feat-joined", main_dollars=1.0, sort_ts=1.0)], [merged_pr],
            )


class TestScanCodeReviewRounds:
    @staticmethod
    def _joined_record(rounds):
        records, _ = review_cost_by_size._join_rounds_to_prs(
            rounds, [_merged_pr_sized(7, "feat", additions=3, deletions=2)],
        )
        assert len(records) == 1
        return records[0]

    def test_rounds_sum_across_roots_when_one_roots_project_dir_is_a_symlink_outside_every_root(
        self, tmp_path, monkeypatch,
    ):
        """Two rounds in root A and three in root B join one PR with five. B's slug-named project directory
        resolves outside both roots, which scope._root_index_for_path would abort on if the scan passed
        resolved roots."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        project_in_root_a = roots[0] / _PROJECT_SLUG
        project_in_root_a.mkdir()
        _write_code_review_session(project_in_root_a / "sess-a.jsonl", "feat", 2)
        external = tmp_path / "external"
        external.mkdir()
        _write_code_review_session(external / "sess-b.jsonl", "feat", 3)
        (roots[1] / _PROJECT_SLUG).symlink_to(external)

        rounds, _scope_label = review_cost_by_size._scan_code_review_rounds(
            _review_cost_by_size_args(this_repo=True, this_repo_slugs=[_PROJECT_SLUG]), roots, Counter(),
        )

        assert self._joined_record(rounds).round_count == 5

    def test_project_dir_aliased_across_roots_is_read_once(self, tmp_path, monkeypatch):
        """B's slug-named directory is a symlink to A's, so the PR's round count equals A's alone."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        project_in_root_a = roots[0] / _PROJECT_SLUG
        project_in_root_a.mkdir()
        _write_code_review_session(project_in_root_a / "sess-a.jsonl", "feat", 2)
        (roots[1] / _PROJECT_SLUG).symlink_to(project_in_root_a)

        rounds, _scope_label = review_cost_by_size._scan_code_review_rounds(
            _review_cost_by_size_args(this_repo=True, this_repo_slugs=[_PROJECT_SLUG]), roots, Counter(),
        )

        assert self._joined_record(rounds).round_count == 2

    def test_rounds_opened_after_a_prs_merge_still_count_toward_it(self, fake_projects):
        """The session's rounds open on 2026-08-01, seven months after the PR's explicit mergedAt."""
        _write_code_review_session(fake_projects / "sess-after-merge.jsonl", "feat", 2)
        rounds, _scope_label = review_cost_by_size._scan_code_review_rounds(
            _review_cost_by_size_args(this_repo=True, this_repo_slugs=[_PROJECT_SLUG]),
            [fake_projects.parent], Counter(),
        )

        records, _counts = review_cost_by_size._join_rounds_to_prs(
            rounds, [{**_merged_pr_sized(7, "feat", additions=3, deletions=2), "mergedAt": "2026-01-01T00:00:00Z"}],
        )

        assert [(record.number, record.round_count) for record in records] == [(7, 2)]

    def test_dispatch_dollars_count_once_and_only_code_review_rounds_count(self, fake_projects):
        """The code-review round dispatches a subagent and sits alone in its session file, so it is that
        session's last round window, where merged subagent records would be priced a second time. Its
        dollars are the opening turn plus the dispatch's own $1.00, once. The plan-review and
        ready-for-review rounds in a second session file never count."""
        _write_jsonl(fake_projects / "sess-code-review.jsonl", [
            _priced(  # opening turn: $0.20, also spawns dispatch a1
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s1", "code-review"), _agent_use("a1", "staff-sdet")],
            ),
        ])
        _write_subagent_dispatch(
            fake_projects, "sess-code-review", "agent-1", "a1",
            [_priced("claude-sonnet-5", input=500_000, branch="feat", ts="2026-08-01T10:00:30.000Z")],  # $1.00
        )
        _write_jsonl(fake_projects / "sess-other-reviews.jsonl", [
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T11:00:00.000Z",
                content=[_skill_block("p1", "plan-review")],
            ),
            _user_msg("next", branch="feat", ts="2026-08-01T11:01:00.000Z"),
            _priced(
                "claude-sonnet-5", input=100_000, branch="feat", ts="2026-08-01T11:02:00.000Z",
                content=[_skill_block("r1", "ready-for-review")],
            ),
        ])

        rounds, _scope_label = review_cost_by_size._scan_code_review_rounds(
            _review_cost_by_size_args(this_repo=True, this_repo_slugs=[_PROJECT_SLUG]),
            [fake_projects.parent], Counter(),
        )

        record = self._joined_record(rounds)
        assert record.round_count == 1
        assert record.total_dollars == pytest.approx(_ROUND_OPENING_TURN_DOLLARS + 1.00)


class TestCmdReviewCostBySize:
    @pytest.mark.parametrize(
        "args_kwargs",
        [
            pytest.param({"this_repo": False}, id="no-this-repo"),
            pytest.param({"this_repo": False, "all_accounts": True}, id="all-accounts-without-this-repo"),
            pytest.param({"this_repo": False, "projects": "x"}, id="projects-without-this-repo"),
            pytest.param({"this_repo": True, "projects": "x"}, id="projects-with-this-repo"),
        ],
    )
    def test_refuses_without_this_repo_before_any_gh_call(self, monkeypatch, capsys, args_kwargs):
        call_log: list[list[str]] = []
        with pytest.raises(SystemExit) as exc_info:
            _run_cmd(monkeypatch, capsys, _review_cost_by_size_args(**args_kwargs), call_log=call_log)
        assert exc_info.value.code == 2
        assert call_log == []
        assert "review-cost-by-size" in capsys.readouterr().err

    def test_default_mode_exits_2_under_its_own_name_when_the_config_dir_is_unresolvable(self, monkeypatch, capsys):
        def unresolvable_config_dir():
            raise ValueError("no HOME")

        monkeypatch.setattr(scope, "config_dir", unresolvable_config_dir)
        call_log: list[list[str]] = []
        with pytest.raises(SystemExit) as exc_info:
            _run_cmd(
                monkeypatch, capsys, _review_cost_by_size_args(this_repo=True, this_repo_slugs=[_PROJECT_SLUG]),
                call_log=call_log,
            )
        assert exc_info.value.code == 2
        assert call_log == []
        assert "review-cost-by-size: no HOME" in capsys.readouterr().err

    @staticmethod
    def _two_root_corpus(tmp_path, monkeypatch) -> list[dict]:
        """Active root: feat-a (2 rounds), the size-tied PRs feat-t1 and feat-t2 (1 round each) and an
        unmerged sentinel branch. Declared root: feat-b (3 rounds). Returns the merged-PR list. The tied
        PRs' session files sort opposite to their PR numbers (sess-t1 holds PR 31, sess-t2 holds PR 30), so
        their records arrive out of PR-number order."""
        roots = _two_declared_roots(tmp_path, monkeypatch)
        active_project = roots[0] / _PROJECT_SLUG
        active_project.mkdir()
        _write_code_review_session(active_project / "sess-a.jsonl", "feat-a", 2)
        _write_code_review_session(active_project / "sess-t1.jsonl", "feat-t1", 1)
        _write_code_review_session(active_project / "sess-t2.jsonl", "feat-t2", 1)
        _write_code_review_session(active_project / "sess-sentinel.jsonl", "zz-unmerged-sentinel", 1)
        declared_project = roots[1] / _PROJECT_SLUG
        declared_project.mkdir()
        _write_code_review_session(declared_project / "sess-b.jsonl", "feat-b", 3)
        return [
            _merged_pr_sized(31, "feat-t1", additions=4, deletions=3),
            _merged_pr_sized(22, "feat-b", additions=20, deletions=5),
            _merged_pr_sized(11, "feat-a", additions=3, deletions=2),
            _merged_pr_sized(30, "feat-t2", additions=6, deletions=1),
            _merged_pr_sized(99, "feat-no-sessions", additions=1, deletions=1),
        ]

    def test_default_mode_reads_the_active_root_alone_and_prints_rows_after_every_aggregate(
        self, tmp_path, monkeypatch, capsys,
    ):
        merged_prs = self._two_root_corpus(tmp_path, monkeypatch)
        call_log: list[list[str]] = []
        out, err = _run_cmd(
            monkeypatch, capsys,
            _review_cost_by_size_args(this_repo=True, this_repo_slugs=[_PROJECT_SLUG]),
            merged_prs=merged_prs, repo="sentinel-org/sentinel-repo", call_log=call_log,
            enforce_repo_pin="sentinel-org/sentinel-repo",
        )
        pr_list_argv = next(argv for argv in call_log if argv[:3] == ["gh", "pr", "list"])
        requested_fields = set(pr_list_argv[pr_list_argv.index("--json") + 1].split(","))
        assert {"number", "headRefName", "additions", "deletions"} <= requested_fields
        lines = out.splitlines()
        assert lines[0] == _HEADER_ACTIVE_ACCOUNT
        assert "scanning root" not in err
        assert "DO NOT PUBLISH" not in out + err
        assert "  PRs joined: 3" in lines
        assert "  Merged PRs listed: 5" in lines
        assert "  Unmatched branches: 1" in lines
        assert any(line.startswith("  0-149 lines: n=3;") for line in lines)
        assert _printed_pr_numbers(out) == [11, 30, 31]
        assert out.index("Aggregates") < out.index("Join diagnostics") < out.index("Per-PR rows")
        for withheld in ("zz-unmerged-sentinel", "sentinel-org", "sentinel-repo", str(tmp_path)):
            assert withheld not in out + err
        # feat-a: two rounds at the opening-turn price.
        assert re.search(r"^\s+11\s+5\s+2\s+\$0\.40\s+\$0\.20$", out, re.MULTILINE)

    def test_a_pr_of_a_thousand_or_more_lines_prints_its_lines_with_a_thousands_separator(
        self, fake_projects, monkeypatch, capsys,
    ):
        _write_code_review_session(fake_projects / "sess-large.jsonl", "feat-large", 2)
        out, _err = _run_cmd(
            monkeypatch, capsys,
            _review_cost_by_size_args(this_repo=True, this_repo_slugs=[_PROJECT_SLUG]),
            merged_prs=[_merged_pr_sized(7, "feat-large", additions=1000, deletions=480)],
        )
        assert "        7     1,480       2       $0.40       $0.20" in out.splitlines()

    def test_origin_remote_host_reaches_the_auth_preflight_repo_pin_and_pr_discovery(
        self, fake_projects, monkeypatch, capsys,
    ):
        """A GitHub Enterprise origin host is the host the preflight, the repo pin and the PR listing use.

        The pin's gh-side host must agree with the origin host, or the run exits 2.
        """
        call_log: list[list[str]] = []
        _run_cmd(
            monkeypatch, capsys,
            _review_cost_by_size_args(this_repo=True, this_repo_slugs=[_PROJECT_SLUG]),
            host="ghe.example.com", repo="ghe-org/ghe-repo", call_log=call_log,
            gh_repo_view_host="ghe.example.com",
        )
        auth_argv = next(argv for argv in call_log if argv[:3] == ["gh", "auth", "status"])
        assert auth_argv[auth_argv.index("--hostname") + 1] == "ghe.example.com"
        assert any(argv[:3] == ["gh", "repo", "view"] for argv in call_log)
        pr_list_argv = next(argv for argv in call_log if argv[:3] == ["gh", "pr", "list"])
        assert pr_list_argv[pr_list_argv.index("--repo") + 1] == "ghe.example.com/ghe-org/ghe-repo"

    def test_all_accounts_merges_every_root_and_marks_both_streams_do_not_publish(
        self, tmp_path, monkeypatch, capsys,
    ):
        merged_prs = self._two_root_corpus(tmp_path, monkeypatch)
        out, err = _run_cmd(
            monkeypatch, capsys,
            _review_cost_by_size_args(this_repo=True, all_accounts=True, this_repo_slugs=[_PROJECT_SLUG]),
            merged_prs=merged_prs,
        )
        lines = out.splitlines()
        assert lines[0] == review_cost_by_size._PRIVATE_VIEW_BANNER
        assert lines[1] == _HEADER_EVERY_ACCOUNT
        assert err.splitlines()[0] == review_cost_by_size._PRIVATE_VIEW_BANNER
        assert "scanning root" in err
        assert "  PRs joined: 4" in lines
        assert any(line.startswith("  0-149 lines: n=4;") for line in lines)
        assert _printed_pr_numbers(out) == [11, 30, 31, 22]

    def test_all_accounts_over_a_single_root_still_marks_both_streams_do_not_publish(
        self, fake_projects, monkeypatch, capsys,
    ):
        out, err = _run_cmd(
            monkeypatch, capsys,
            _review_cost_by_size_args(this_repo=True, all_accounts=True, this_repo_slugs=[_PROJECT_SLUG]),
        )
        assert out.splitlines()[0] == review_cost_by_size._PRIVATE_VIEW_BANNER
        assert err.splitlines()[0] == review_cost_by_size._PRIVATE_VIEW_BANNER
        assert "scanning root" not in err

    def test_unreadable_session_files_are_counted_and_the_readable_one_still_joins(self, fake_projects, monkeypatch, capsys):
        if os.geteuid() == 0:
            pytest.skip("root bypasses file permission bits")
        _write_code_review_session(fake_projects / "readable.jsonl", "feat-a", 1)
        sealed_files = [fake_projects / "sealed-1.jsonl", fake_projects / "sealed-2.jsonl"]
        for sealed in sealed_files:
            _write_code_review_session(sealed, "feat-a", 1)
            os.chmod(sealed, 0o000)
        try:
            out, _err = _run_cmd(
                monkeypatch, capsys,
                _review_cost_by_size_args(this_repo=True, this_repo_slugs=[_PROJECT_SLUG]),
                merged_prs=[_merged_pr_sized(11, "feat-a", additions=3, deletions=2)],
            )
        finally:
            for sealed in sealed_files:
                os.chmod(sealed, 0o644)
        lines = out.splitlines()
        assert "  Unreadable sources skipped: 2" in lines
        assert "  PRs joined: 1" in lines

    def test_unreadable_sources_at_different_scan_levels_are_summed_across_roots(self, tmp_path, monkeypatch, capsys):
        """A sealed session file in one root and a sealed project directory in the other count as 2.

        The count sums the session-file and project-dir gap levels across both roots, so a count that read one
        level alone would report 1.
        """
        if os.geteuid() == 0:
            pytest.skip("root bypasses file permission bits")
        roots = _two_declared_roots(tmp_path, monkeypatch)
        active_project = roots[0] / _PROJECT_SLUG
        active_project.mkdir()
        _write_code_review_session(active_project / "readable.jsonl", "feat-a", 1)
        sealed_file = active_project / "sealed.jsonl"
        _write_code_review_session(sealed_file, "feat-a", 1)
        sealed_project_dir = roots[1] / _PROJECT_SLUG
        sealed_project_dir.mkdir()
        os.chmod(sealed_file, 0o000)
        os.chmod(sealed_project_dir, 0o000)
        try:
            out, _err = _run_cmd(
                monkeypatch, capsys,
                _review_cost_by_size_args(this_repo=True, all_accounts=True, this_repo_slugs=[_PROJECT_SLUG]),
                merged_prs=[_merged_pr_sized(11, "feat-a", additions=3, deletions=2)],
            )
        finally:
            os.chmod(sealed_file, 0o644)
            os.chmod(sealed_project_dir, 0o755)
        lines = out.splitlines()
        assert "  Unreadable sources skipped: 2" in lines
        assert "  PRs joined: 1" in lines

    @pytest.mark.parametrize("all_accounts", [False, True], ids=["default-mode", "all-accounts"])
    def test_failing_gh_auth_exits_1_before_any_report_or_scan(self, tmp_path, monkeypatch, capsys, all_accounts):
        self._two_root_corpus(tmp_path, monkeypatch)
        scan_calls: list[tuple] = []
        monkeypatch.setattr(
            review_cost_by_size, "_scan_code_review_rounds", lambda *scan_args: scan_calls.append(scan_args),
        )
        with pytest.raises(SystemExit) as exc_info:
            _run_cmd(
                monkeypatch, capsys,
                _review_cost_by_size_args(this_repo=True, all_accounts=all_accounts, this_repo_slugs=[_PROJECT_SLUG]),
                gh_auth_status_failure=True,
            )
        assert exc_info.value.code == 1
        out, err = capsys.readouterr()
        error_lines = err.splitlines()
        assert scan_calls == []
        assert "scanning root" not in err
        if all_accounts:
            assert out == review_cost_by_size._PRIVATE_VIEW_BANNER + "\n"
            assert error_lines[0] == review_cost_by_size._PRIVATE_VIEW_BANNER
            assert error_lines[1].startswith("review-cost-by-size: gh auth status failed")
        else:
            assert out == ""
            assert error_lines[0].startswith("review-cost-by-size: gh auth status failed")

    @pytest.mark.parametrize("all_accounts", [False, True], ids=["default-mode", "all-accounts"])
    def test_failing_origin_remote_lookup_exits_1_under_this_commands_name(
        self, fake_projects, monkeypatch, capsys, all_accounts,
    ):
        """The helper's `subcommand` default is `pr-cost`; the failure message must name this command."""
        remaining_calls_double = _fake_pr_cost_subprocess_run()

        def origin_remote_lookup_fails(cmd, *args, **kwargs):
            if cmd[:3] == ["git", "remote", "get-url"]:
                raise subprocess.CalledProcessError(128, cmd)
            return remaining_calls_double(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", origin_remote_lookup_fails)
        with pytest.raises(SystemExit) as exc_info:
            review_cost_by_size.cmd_review_cost_by_size(
                _review_cost_by_size_args(this_repo=True, all_accounts=all_accounts, this_repo_slugs=[_PROJECT_SLUG]),
            )
        assert exc_info.value.code == 1
        out, err = capsys.readouterr()
        error_lines = err.splitlines()
        if all_accounts:
            assert out == review_cost_by_size._PRIVATE_VIEW_BANNER + "\n"
            assert error_lines[0] == review_cost_by_size._PRIVATE_VIEW_BANNER
            assert error_lines[1].startswith("review-cost-by-size: could not resolve this repo's own remote")
        else:
            assert out == ""
            assert error_lines[0].startswith("review-cost-by-size: could not resolve this repo's own remote")

    @pytest.mark.parametrize("all_accounts", [False, True], ids=["default-mode", "all-accounts"])
    def test_failing_merged_pr_discovery_exits_1_before_any_scan_or_report(
        self, fake_projects, monkeypatch, capsys, all_accounts,
    ):
        scan_calls: list[tuple] = []
        monkeypatch.setattr(
            review_cost_by_size, "_scan_code_review_rounds", lambda *scan_args: scan_calls.append(scan_args),
        )
        remaining_calls_double = _fake_pr_cost_subprocess_run()
        pr_list_calls: list[list[str]] = []
        requested_sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda seconds: requested_sleeps.append(seconds))

        def merged_pr_discovery_fails(cmd, *args, **kwargs):
            if cmd[:3] == ["gh", "pr", "list"]:
                pr_list_calls.append(cmd)
                # Auth-shaped stderr is never retried.
                return subprocess.CompletedProcess(cmd, 1, "", "error: not logged into any GitHub hosts\n")
            return remaining_calls_double(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", merged_pr_discovery_fails)
        with pytest.raises(SystemExit) as exc_info:
            review_cost_by_size.cmd_review_cost_by_size(
                _review_cost_by_size_args(this_repo=True, all_accounts=all_accounts, this_repo_slugs=[_PROJECT_SLUG]),
            )
        assert exc_info.value.code == 1
        out, err = capsys.readouterr()
        error_lines = err.splitlines()
        assert len(pr_list_calls) == 1
        assert requested_sleeps == []
        assert scan_calls == []
        # The abort label's command prefix is shared-helper wording, so only the fragment after it is pinned.
        assert "gh pr list failed (degraded_auth) before any row could be captured" in error_lines[-1]
        if all_accounts:
            assert out == review_cost_by_size._PRIVATE_VIEW_BANNER + "\n"
            assert error_lines[0] == review_cost_by_size._PRIVATE_VIEW_BANNER
        else:
            assert out == ""

    def test_limit_note_prints_only_when_the_merged_list_reaches_the_gh_limit(self, fake_projects, monkeypatch, capsys):
        """No sessions are in scope, so each run is the empty scope: the full report with n/a aggregates."""
        monkeypatch.setattr(gh_cli, "_PR_COST_GH_PR_LIST_LIMIT", 3)
        merged_prs = [_merged_pr_sized(number, f"feat-{number}", additions=1, deletions=1) for number in (1, 2, 3)]
        args = _review_cost_by_size_args(this_repo=True, this_repo_slugs=[_PROJECT_SLUG])

        out_below_limit, _err = _run_cmd(monkeypatch, capsys, args, merged_prs=merged_prs[:2])
        out_at_limit, _err = _run_cmd(monkeypatch, capsys, args, merged_prs=merged_prs)

        assert "gh pr list limit" not in out_below_limit
        assert "gh pr list limit" in out_at_limit
        assert "can join the wrong PR" in out_at_limit
        assert "can join the wrong PR" not in out_below_limit
        for out in (out_below_limit, out_at_limit):
            assert "  rounds per PR: n/a" in out
            assert "  PRs joined: 0" in out.splitlines()

    def test_gh_repo_view_naming_another_repo_exits_2_before_merged_pr_discovery(
        self, fake_projects, monkeypatch, capsys,
    ):
        """The origin remote and `gh repo view` name different repos, so the pin refuses before any
        `gh pr list` call. Without the pin the run would list the merged PRs and print a report."""
        call_log: list[list[str]] = []
        with pytest.raises(SystemExit) as exc_info:
            _run_cmd(
                monkeypatch, capsys,
                _review_cost_by_size_args(this_repo=True, this_repo_slugs=[_PROJECT_SLUG]),
                merged_prs=[_merged_pr_sized(11, "feat-a", additions=3, deletions=2)],
                repo="sentinel-org/sentinel-repo", gh_repo_name_with_owner="other-org/other-repo",
                call_log=call_log,
            )
        assert exc_info.value.code == 2
        assert ["gh", "repo", "view"] in [argv[:3] for argv in call_log]
        assert not any(argv[:3] == ["gh", "pr", "list"] for argv in call_log)
        out, err = capsys.readouterr()
        assert out == ""
        assert "does not match this repo's own git remote identity" in err
        for withheld in ("sentinel-org", "sentinel-repo", "other-org", "other-repo"):
            assert withheld not in err

    def test_private_view_banner_cites_a_real_heading(self):
        banner = review_cost_by_size._PRIVATE_VIEW_BANNER
        assert banner.startswith("DO NOT PUBLISH")
        cited = re.search(r'§\s+"([^"\n]+)"', banner)
        assert cited, f"{banner!r} does not cite a heading in the § \"...\" form"
        assert normalize_heading(cited.group(1)) in heading_texts(_PRIVATE_PROJECT_REDACTION_DOC.read_text())
