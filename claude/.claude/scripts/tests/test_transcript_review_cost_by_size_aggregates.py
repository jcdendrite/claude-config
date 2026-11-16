"""Tests for review_cost_by_size.py's aggregates and its printed aggregate and join-diagnostics lines."""
import math

import pytest
from transcript_analysis import review_cost_by_size


def _record(number: int, lines: int, *round_dollars: float) -> review_cost_by_size._PrRecord:
    return review_cost_by_size._PrRecord(number=number, lines=lines, round_dollars=tuple(round_dollars))


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
            unpriced_turns=44, dangling_dispatches=55, invalid_usage_records=1_234,
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
            "  MALFORMED USAGE — 1,234 usage-bearing turns inside joined rounds had non-numeric or negative"
            " token fields and were excluded from this report's dollars",
            "  Unreadable sources skipped: 77",
        ]

    def test_join_diagnostics_print_no_malformed_usage_line_when_no_turn_was_excluded(self, capsys):
        counts = review_cost_by_size._JoinCounts(
            merged_pr_count=11, unmatched_branch_count=22, ambiguous_branch_count=33,
            unpriced_turns=44, dangling_dispatches=55, invalid_usage_records=0,
        )
        review_cost_by_size._print_join_diagnostics(66, counts, 77)
        assert "MALFORMED USAGE" not in capsys.readouterr().out
