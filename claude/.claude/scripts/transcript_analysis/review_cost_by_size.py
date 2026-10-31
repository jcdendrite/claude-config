"""The review-cost-by-size command: cmd_review_cost_by_size and its helpers -- how
code-review round count and round cost vary with merged-PR size, joined to each PR by exact
head-branch name -- plus its subparser registration.

Imports gh_cli, render, review_rounds, and scope by module (attribute access, not by
name) -- see scope.py's own top-of-file comment for why.
"""
from __future__ import annotations

import argparse
import math
import statistics
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

from transcript_analysis import gh_cli, render, review_rounds, scope

_SIZED_REVIEW_SKILL = "code-review"

# Lower edges, in changed lines (additions + deletions), of half-open size buckets; each bucket runs to
# the next edge and the last is unbounded. The edges follow the bucket bounds named in GitHub issue #1364.
_SIZE_BUCKET_LOWER_EDGES: tuple[int, ...] = (0, 150, 400, 800)

# The smallest round count whose rank correlation of round index against round dollars can take more than a
# few distinct values.
_MIN_ROUNDS_FOR_TREND = 4

# Every figure this command prints is measured per merged PR.
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

_PRIVATE_VIEW_BANNER = (
    "DO NOT PUBLISH — --all-accounts reads every declared account, so this output is a private view for the"
    " owner; see docs/private-project-redaction.md § \"A wider corpus goes to the owner, never into a public"
    " artifact\"."
)


class _PrRecord(NamedTuple):
    """One merged PR with at least one in-scope code-review round: its number, its size in changed
    lines, and each round's dollars in round order."""

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


class _JoinCounts(NamedTuple):
    merged_pr_count: int
    unmatched_branch_count: int
    ambiguous_branch_count: int
    unpriced_turns: int
    dangling_dispatches: int


def _scan_code_review_rounds(
    args: argparse.Namespace, roots: Sequence[Path], scan_gaps: Counter[str],
) -> tuple[list[dict], str]:
    """Every code-review round across `roots`, with the scope label the resolver returned.

    Passes resolved_roots=None so a branch's rounds from every root merge under one (None, branch) key,
    and scope._root_index_for_path is never reached. The iterator keeps include_subagents=False, which
    compute_review_round_costs requires: subagent records merged into a session would be priced a second
    time into that session's last round window.
    """
    session_iter, scope_label = scope._resolve_project_scope(
        args, "review-cost-by-size", roots=roots, scan_gaps=scan_gaps,
    )
    data = review_rounds.compute_review_round_costs(
        session_iter, skill_filter={_SIZED_REVIEW_SKILL}, resolved_roots=None,
    )
    return data["rounds"], scope_label


def _join_rounds_to_prs(rounds: Sequence[dict], merged_prs: Sequence[dict]) -> tuple[list[_PrRecord], _JoinCounts]:
    """Join rounds to merged PRs by exact head-branch name.

    A branch with no merged PR, or one that never joins (a default-branch candidate or a branch no PR can
    be opened from), counts as unmatched. A branch matching more than one merged PR counts as ambiguous
    and is excluded. Unpriced turns and dangling dispatches are summed over joined rounds only.
    """
    prs_by_head_branch: dict[str, list[dict]] = {}
    for pr in merged_prs:
        prs_by_head_branch.setdefault(pr["headRefName"], []).append(pr)

    rounds_by_branch: dict[str, list[dict]] = {}
    for round_entry in rounds:
        rounds_by_branch.setdefault(round_entry["branch_key"][1], []).append(round_entry)

    records: list[_PrRecord] = []
    unmatched_branch_count = 0
    ambiguous_branch_count = 0
    unpriced_turns = 0
    dangling_dispatches = 0
    for branch, branch_rounds in rounds_by_branch.items():
        if branch in scope._DEFAULT_BRANCH_CANDIDATES or scope._names_no_pr_branch(branch):
            unmatched_branch_count += 1
            continue
        matches = prs_by_head_branch.get(branch, [])
        if not matches:
            unmatched_branch_count += 1
            continue
        if len(matches) > 1:
            ambiguous_branch_count += 1
            continue
        pr = matches[0]
        ordered_rounds = sorted(branch_rounds, key=lambda round_entry: round_entry["sort_key"])
        records.append(_PrRecord(
            number=pr["number"],
            lines=pr["additions"] + pr["deletions"],
            round_dollars=tuple(entry["main_dollars"] + entry["agent_dollars"] for entry in ordered_rounds),
        ))
        unpriced_turns += sum(entry["unpriced_turns"] for entry in ordered_rounds)
        dangling_dispatches += sum(entry["dangling"] for entry in ordered_rounds)

    return records, _JoinCounts(
        merged_pr_count=len(merged_prs),
        unmatched_branch_count=unmatched_branch_count,
        ambiguous_branch_count=ambiguous_branch_count,
        unpriced_turns=unpriced_turns,
        dangling_dispatches=dangling_dispatches,
    )


def _average_ranks(values: Sequence[float]) -> list[float]:
    """1-based ranks of `values`, each run of tied values taking the mean of the positions it spans."""
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    run_start = 0
    while run_start < len(order):
        run_end = run_start
        while run_end + 1 < len(order) and values[order[run_end + 1]] == values[order[run_start]]:
            run_end += 1
        mean_position = (run_start + run_end) / 2 + 1
        for position in range(run_start, run_end + 1):
            ranks[order[position]] = mean_position
        run_start = run_end + 1
    return ranks


def _spearman_rho(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Spearman's rank correlation, or None when it is undefined (fewer than two points or a constant series).

    statistics.correlation(method="ranked") would do this directly but needs Python 3.12, and the repo's
    floor is 3.11.
    """
    try:
        return statistics.correlation(_average_ranks(xs), _average_ranks(ys))
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
    - buckets: one dict per size bucket (lower, upper, pr_count, medians by measure).
    - trend: the result of _compute_within_pr_trend.
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


def _scope_header(scope_label: str, all_accounts: bool) -> str:
    account_scope = "every declared account" if all_accounts else "active account only"
    return f"REVIEW COST BY SIZE SOURCES ({scope_label}; {account_scope})"


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


def _print_join_diagnostics(joined_pr_count: int, counts: _JoinCounts, scan_gap_count: int) -> None:
    print("\nJoin diagnostics")
    print(f"  Merged PRs listed: {counts.merged_pr_count}")
    if counts.merged_pr_count >= gh_cli._PR_COST_GH_PR_LIST_LIMIT:
        print(
            f"  Note: the merged-PR list reached the gh pr list limit ({gh_cli._PR_COST_GH_PR_LIST_LIMIT});"
            " older merged PRs may count as unmatched branches, and a branch name reused by a PR beyond the limit"
            " can join the wrong PR."
        )
    print(f"  PRs joined: {joined_pr_count}")
    print(f"  Unmatched branches: {counts.unmatched_branch_count}")
    print(f"  Ambiguous branches (excluded): {counts.ambiguous_branch_count}")
    print(f"  Unpriced turns inside joined rounds: {counts.unpriced_turns}")
    print(f"  Dangling dispatches inside joined rounds: {counts.dangling_dispatches}")
    print(f"  Unreadable sources skipped: {scan_gap_count}")


def _print_pr_rows(records: Sequence[_PrRecord]) -> None:
    print("\nPer-PR rows, sorted by lines then PR number")
    print(f"  {'PR':>7}  {'lines':>8}  {'rounds':>6}  {'total $':>10}  {'mean $':>10}")
    for record in sorted(records, key=lambda record: (record.lines, record.number)):
        print(
            f"  {record.number:>7}  {record.lines:>8,}  {record.round_count:>6}"
            f"  {render._fmt_usd(record.total_dollars):>10}  {render._fmt_usd(record.mean_dollars):>10}"
        )


def cmd_review_cost_by_size(args: argparse.Namespace) -> None:
    """How code-review round count and round cost vary with merged-PR size.

    See docs/transcript-analysis.md's `review-cost-by-size` section for the flags, join rules, aggregates and
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
    if not gh_cli._gh_auth_preflight_ok(corpus_host):
        print(
            "review-cost-by-size: gh auth status failed (or gh is unavailable) -- run `gh auth login`",
            file=sys.stderr,
        )
        sys.exit(1)
    redact_ordinals = scope._redaction_ordinals(roots)
    pinned_repo, _repo_map = gh_cli._resolve_pinned_gh_repo(
        corpus_host, corpus_repo, ordinal=redact_ordinals[roots[0].resolve()],
    )
    merged_prs = gh_cli._gh_discover_merged_prs(corpus_host, pinned_repo)

    scan_gaps: Counter[str] = Counter()
    rounds, scope_label = _scan_code_review_rounds(args, roots, scan_gaps)
    records, join_counts = _join_rounds_to_prs(rounds, merged_prs)

    print(_scope_header(scope_label, all_accounts))
    _print_aggregates(_compute_aggregates(records))
    _print_join_diagnostics(len(records), join_counts, sum(scan_gaps.values()))
    _print_pr_rows(records)


def _add_review_cost_by_size_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the review-cost-by-size subparser on build_parser()'s subparsers action,
    binding cmd_review_cost_by_size through set_defaults(func=...).
    """
    p_review_cost_by_size = subparsers.add_parser(
        "review-cost-by-size",
        help=(
            "How code-review round count and round cost vary with merged-PR size: per-PR rows plus rank"
            " correlations, log-log elasticities, size-bucket medians, and a within-PR round-cost trend."
            " Requires --this-repo and gh."
        ),
    )
    scope._add_project_scope_args(p_review_cost_by_size)
    p_review_cost_by_size.add_argument(
        "--all-accounts", action="store_true",
        help=(
            "Read every declared account as one merged scan instead of the active account alone."
            " A private view: prints DO NOT PUBLISH, and its output is never proposed for publication."
        ),
    )
    p_review_cost_by_size.set_defaults(func=cmd_review_cost_by_size)
