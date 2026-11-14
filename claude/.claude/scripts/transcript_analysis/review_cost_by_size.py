"""The review-cost-by-size command: cmd_review_cost_by_size and its helpers -- how
code-review round count and round cost vary with merged-PR size, read from the pr-cost
ledger's review_rounds cells -- plus its subparser registration.

Imports gh_cli, ledger_rounds, pr_cost_ledger, render, and scope by module (attribute access, not by
name) -- see scope.py's own top-of-file comment for why.
"""
from __future__ import annotations

import argparse
import math
import statistics
import sys
from collections.abc import Sequence
from typing import NamedTuple

from transcript_analysis import gh_cli, ledger_rounds, pr_cost_ledger, render, scope

_SIZED_REVIEW_SKILL = "code-review"

# Half-open size buckets on changed lines (additions + deletions), with bounds from GitHub issue #1364.
_SIZE_BUCKET_LOWER_EDGES: tuple[int, ...] = (0, 150, 400, 800)

# The smallest round count whose rank correlation of round index against round dollars can take more than a
# few distinct values.
_MIN_ROUNDS_FOR_TREND = 4

_ROUND_COUNT = "round_count"
_TOTAL_DOLLARS = "total_dollars"
_MEAN_DOLLARS = "mean_dollars"
_MEASURES: tuple[str, ...] = (_ROUND_COUNT, _TOTAL_DOLLARS, _MEAN_DOLLARS)
_MEASURE_LABELS: dict[str, str] = {
    _ROUND_COUNT: "rounds per PR",
    _TOTAL_DOLLARS: "total dollars per PR",
    _MEAN_DOLLARS: "mean dollars per round",
}

_NOT_AVAILABLE = "n/a"

_SCOPE_UNVERIFIED_NOTICE = (
    "Scope unverified -- a pr-cost ledger row does not record which project directories its --record run read, so a"
    " PR's rounds can include another repository's session on a same-named branch; see docs/transcript-analysis.md"
    " § \"review-cost-by-size\"."
)
_NO_PR_REPORTED_MESSAGE = (
    "No PR reported. A row recorded before the review_rounds column needs pr-cost --record --force --pr N to refill"
    " it. That re-capture is safe only while every session of that PR is inside cleanupPeriodDays; outside it, it"
    " permanently understates the PR's row. The ledger diagnostics below count the PRs"
    " left out; see docs/transcript-analysis.md § \"review-cost-by-size\"."
)
_NO_PR_RECORDED_MESSAGE = (
    "No PR reported. The ledger read above has no row for this repository. Run pr-cost --record to capture merged PRs"
    " past the as-of window; it needs the pr_cost_recording config key (see docs/pr-cost.md § \"Data\")."
)

_PRIVATE_VIEW_BANNER = (
    "DO NOT PUBLISH — --all-accounts reads every declared account, so this output is a private view for the"
    " owner; see docs/private-project-redaction.md § \"A wider corpus goes to the owner, never into a public"
    " artifact\"."
)


class _PrRecord(NamedTuple):
    """One recorded PR with at least one code-review round before merge."""

    number: int
    lines: int
    round_dollars: tuple[float, ...]

    @property
    def round_count(self) -> int:
        return len(self.round_dollars)

    @property
    def total_dollars(self) -> float:
        return sum(self.round_dollars)

    @property
    def mean_dollars(self) -> float:
        return self.total_dollars / self.round_count


def _spearman_rho(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Spearman's rank correlation, or None when it is undefined (fewer than two points or a constant series)."""
    try:
        return statistics.correlation(xs, ys, method="ranked")
    except statistics.StatisticsError:
        return None


def _log_log_slope(lines: Sequence[int], values: Sequence[float]) -> tuple[float | None, int]:
    """Slope of ln(value) against ln(lines) over the pairs where both are positive, and how many pairs that is.

    Constant series are decided on the pre-log inputs, because statistics.linear_regression misjudges them after the log.
    """
    surviving = [(line_count, value) for line_count, value in zip(lines, values, strict=True)
                 if line_count > 0 and value > 0]
    pair_count = len(surviving)
    if len({line_count for line_count, _ in surviving}) < 2:
        return None, pair_count
    if len({value for _, value in surviving}) == 1:
        return 0.0, pair_count
    try:
        slope = statistics.linear_regression(
            [math.log(line_count) for line_count, _ in surviving],
            [math.log(value) for _, value in surviving],
        ).slope
    except statistics.StatisticsError:
        slope = None
    return slope, pair_count


def _median_or_none(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def _size_bucket_bounds() -> list[tuple[int, int | None]]:
    uppers: list[int | None] = [*_SIZE_BUCKET_LOWER_EDGES[1:], None]
    return list(zip(_SIZE_BUCKET_LOWER_EDGES, uppers, strict=True))


def _compute_within_pr_trend(records: Sequence[_PrRecord]) -> dict:
    """Spearman's rho of round index against round dollars for each PR with enough rounds, summarized.
    A PR whose rho is undefined (every round cost the same) is skipped."""
    rhos: list[float] = []
    for record in records:
        if record.round_count < _MIN_ROUNDS_FOR_TREND:
            continue
        rho = _spearman_rho(range(1, record.round_count + 1), record.round_dollars)
        if rho is not None:
            rhos.append(rho)
    return {
        "pr_count": len(rhos),
        "median_rho": _median_or_none(rhos),
        "share_positive": sum(1 for rho in rhos if rho > 0) / len(rhos) if rhos else None,
    }


def _compute_aggregates(records: Sequence[_PrRecord]) -> dict:
    """Every aggregate the report prints, with None wherever a value is undefined.

    Keys:
    - spearman: measure -> (rho, PRs ranked). Every PR is ranked, so the count is len(records) for all measures.
    - elasticity: measure -> (slope, PRs used).
    """
    lines = [record.lines for record in records]
    measured = {measure: [getattr(record, measure) for record in records] for measure in _MEASURES}

    buckets = []
    for lower, upper in _size_bucket_bounds():
        in_bucket = [record for record in records if record.lines >= lower and (upper is None or record.lines < upper)]
        buckets.append({
            "lower": lower,
            "upper": upper,
            "pr_count": len(in_bucket),
            "medians": {
                measure: _median_or_none([getattr(record, measure) for record in in_bucket]) for measure in _MEASURES
            },
        })

    return {
        "spearman": {measure: (_spearman_rho(lines, measured[measure]), len(records)) for measure in _MEASURES},
        "elasticity": {measure: _log_log_slope(lines, measured[measure]) for measure in _MEASURES},
        "buckets": buckets,
        "trend": _compute_within_pr_trend(records),
    }


def _fmt_optional(value: float | None, number_format: str) -> str:
    return _NOT_AVAILABLE if value is None else format(value, number_format)


def _fmt_measure(measure: str, value: float | None) -> str:
    if value is None:
        return _NOT_AVAILABLE
    return f"{value:.1f}" if measure == _ROUND_COUNT else render._fmt_usd(value)


def _scope_header(all_accounts: bool, ledger_path_overridden: bool) -> str:
    """The override label omits the variable's value and any path. It reads "account scope unverified" because one file
    may hold several accounts."""
    if ledger_path_overridden:
        account_scope = "PR_COST_LEDGER_PATH file, account scope unverified"
    else:
        account_scope = "every declared account" if all_accounts else "active account only"
    return f"REVIEW COST BY SIZE SOURCES (pr-cost ledger rows for this checkout's origin; {account_scope})"


def _bucket_label(lower: int, upper: int | None) -> str:
    return f"{lower}+ lines" if upper is None else f"{lower}-{upper - 1} lines"


def _print_aggregates(aggregates: dict) -> None:
    print("\nAggregates -- code-review rounds per merged PR; size is additions + deletions")
    print("Spearman rho of lines against (rho; PRs used):")
    for measure in _MEASURES:
        rho, ranked_pr_count = aggregates["spearman"][measure]
        print(f"  {_MEASURE_LABELS[measure]}: {_fmt_optional(rho, '.2f')} (n={ranked_pr_count})")
    print("Log-log elasticity against lines (slope; PRs used):")
    for measure in _MEASURES:
        slope, pr_count = aggregates["elasticity"][measure]
        print(f"  {_MEASURE_LABELS[measure]}: {_fmt_optional(slope, '.2f')} (n={pr_count})")
    print("Size buckets, median per PR:")
    for bucket in aggregates["buckets"]:
        medians = "; ".join(
            f"{_MEASURE_LABELS[measure]} {_fmt_measure(measure, bucket['medians'][measure])}" for measure in _MEASURES
        )
        print(f"  {_bucket_label(bucket['lower'], bucket['upper'])}: n={bucket['pr_count']}; {medians}")
    trend = aggregates["trend"]
    print(f"Within-PR trend, rho of round index against round dollars, PRs with at least {_MIN_ROUNDS_FOR_TREND} rounds:")
    print(
        f"  PRs with a rho: {trend['pr_count']}; median rho {_fmt_optional(trend['median_rho'], '.2f')};"
        f" share above zero {_fmt_optional(trend['share_positive'], '.1%')}"
    )


class _ReportedRoundSums(NamedTuple):
    unpriced_turns: int
    dangling_dispatches: int
    invalid_usage_records: int


def _sum_reported_round_fields(ledger_prs: Sequence[ledger_rounds.LedgerPr]) -> _ReportedRoundSums:
    """Unpriced turns, dangling dispatches and malformed-usage turns, summed over the code-review rounds before merge of
    `ledger_prs`."""
    rounds = [each for pr in ledger_prs for each in pr.rounds_by_skill.get(_SIZED_REVIEW_SKILL, [])]
    return _ReportedRoundSums(
        unpriced_turns=sum(each["unpriced_turns"] for each in rounds),
        dangling_dispatches=sum(each["dangling"] for each in rounds),
        invalid_usage_records=sum(each["invalid_usage_records"] for each in rounds),
    )


def _print_ledger_diagnostics(
    ledger_read: ledger_rounds.LedgerRead, reported_prs: Sequence[ledger_rounds.LedgerPr],
) -> None:
    sums = _sum_reported_round_fields(reported_prs)
    print("\nLedger diagnostics")
    print(f"  PRs with a current row for this repository: {ledger_read.pr_count}")
    print(f"  PRs reported: {len(reported_prs)}")
    for reason in ledger_rounds.EXCLUSION_REASONS:
        print(f"  PRs excluded, {reason}: {ledger_read.excluded[reason]}")
    print(f"  PRs with no code-review round before merge: {len(ledger_read.prs) - len(reported_prs)}")
    print(
        "  Code-review rounds removed as opened at or after merge:"
        f" {ledger_read.after_merge_rounds.get(_SIZED_REVIEW_SKILL, 0)}"
    )
    print(f"  Unpriced turns inside reported rounds: {sums.unpriced_turns}")
    print(f"  Dangling dispatches inside reported rounds: {sums.dangling_dispatches}")
    if sums.invalid_usage_records:
        print(
            f"  MALFORMED USAGE — {sums.invalid_usage_records:,} usage-bearing turns inside reported rounds had"
            " non-numeric or negative token fields and were excluded from this report's dollars"
        )


def _print_pr_rows(records: Sequence[_PrRecord]) -> None:
    print("\nPer-PR rows, sorted by lines then PR number")
    print(f"  {'PR':>7}  {'lines':>8}  {'rounds':>6}  {'total $':>10}  {'mean $':>10}")
    for record in sorted(records, key=lambda record: (record.lines, record.number)):
        print(
            f"  {record.number:>7}  {record.lines:>8,}  {record.round_count:>6}"
            f"  {render._fmt_usd(record.total_dollars):>10}  {render._fmt_usd(record.mean_dollars):>10}"
        )


def _record_from_ledger_pr(pr: ledger_rounds.LedgerPr) -> _PrRecord:
    return _PrRecord(number=pr.number, lines=pr.lines, round_dollars=tuple(pr.usd_by_skill[_SIZED_REVIEW_SKILL]))


def cmd_review_cost_by_size(args: argparse.Namespace) -> None:
    """How code-review round count and round cost vary with recorded-PR size.

    See docs/transcript-analysis.md's `review-cost-by-size` section for the flags, row-selection rules, aggregates and
    publication scope.
    """
    this_repo = bool(getattr(args, "this_repo", False))
    projects_arg = getattr(args, "projects", None)
    if not this_repo or projects_arg not in (None, "*"):
        print(
            "review-cost-by-size: requires --this-repo and refuses any --projects scope"
            " -- see docs/transcript-analysis.md",
            file=sys.stderr,
        )
        sys.exit(2)

    all_accounts = bool(getattr(args, "all_accounts", False))
    if all_accounts:
        print(_PRIVATE_VIEW_BANNER)
        print(_PRIVATE_VIEW_BANNER, file=sys.stderr)
        roots = scope.resolve_scan_roots(args)
    else:
        try:
            roots = [scope.config_dir() / "projects"]
        except ValueError as exc:
            print(f"review-cost-by-size: {exc}", file=sys.stderr)
            sys.exit(2)

    corpus_host, corpus_repo = gh_cli._git_remote_origin_host_and_owner_repo(subcommand="review-cost-by-size")
    ledger_read = ledger_rounds.read_ledger_prs(roots, corpus_host, corpus_repo, subcommand="review-cost-by-size")
    reported_prs = [pr for pr in ledger_read.prs if pr.usd_by_skill.get(_SIZED_REVIEW_SKILL)]
    records = [_record_from_ledger_pr(pr) for pr in reported_prs]

    print(_scope_header(all_accounts, pr_cost_ledger.pr_cost_ledger_path_overridden()))
    print(_SCOPE_UNVERIFIED_NOTICE)
    if not records:
        print(_NO_PR_RECORDED_MESSAGE if ledger_read.pr_count == 0 else _NO_PR_REPORTED_MESSAGE)
    _print_aggregates(_compute_aggregates(records))
    _print_ledger_diagnostics(ledger_read, reported_prs)
    _print_pr_rows(records)


def _add_review_cost_by_size_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the review-cost-by-size subparser on build_parser()'s subparsers action,
    binding cmd_review_cost_by_size through set_defaults(func=...).
    """
    p_review_cost_by_size = subparsers.add_parser(
        "review-cost-by-size",
        help=(
            "How code-review round count and round cost vary with recorded-PR size: per-PR rows plus rank"
            " correlations, log-log elasticities, size-bucket medians, and a within-PR round-cost trend."
            " Requires --this-repo and a pr-cost ledger; makes no gh call."
        ),
    )
    scope._add_project_scope_args(
        p_review_cost_by_size,
        this_repo_help=(
            "Select the pr-cost ledger rows recorded for this checkout's origin repository"
            " (see docs/transcript-analysis.md)."
        ),
    )
    p_review_cost_by_size.add_argument(
        "--all-accounts", action="store_true",
        help=(
            "Read every declared account's pr-cost ledger instead of the active account's alone. A PR with"
            " more than one row that carries a review_rounds cell is excluded and counted, not summed."
            " A private view: prints DO NOT PUBLISH, and its output is never proposed for publication."
        ),
    )
    p_review_cost_by_size.set_defaults(func=cmd_review_cost_by_size)
