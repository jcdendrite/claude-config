"""Reads the pr-cost ledger's review_rounds cells for one repository.

Imports pr_cost_ledger, pricing, review_rounds, and scope by module (attribute access, not by name) -- see scope.py's
own top-of-file comment for why.
"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

from transcript_analysis import pr_cost_ledger, pricing, review_rounds, scope

# Printed labels, one per exclusion, in the order a PR is checked against them: the first match counts.
EXCLUDED_NO_CELL = "no review_rounds cell recorded"
EXCLUDED_MULTI_ROW = "more than one row with a review_rounds cell, see docs/transcript-analysis.md"
EXCLUDED_JOIN_CONFIDENCE_LOW = "join confidence low"
EXCLUDED_RULE = "review-round rule other than the current one"
EXCLUDED_UNPRICEABLE_ROUND = "a round this checkout cannot price"
EXCLUSION_REASONS: tuple[str, ...] = (
    EXCLUDED_NO_CELL, EXCLUDED_MULTI_ROW, EXCLUDED_JOIN_CONFIDENCE_LOW, EXCLUDED_RULE, EXCLUDED_UNPRICEABLE_ROUND,
)


class LedgerPr(NamedTuple):
    """One kept PR. `rounds_by_skill` and `usd_by_skill` share keys, and each skill's dollars list runs parallel to
    its rounds list."""
    number: int
    lines: int
    rounds_by_skill: dict[str, list[dict]]
    usd_by_skill: dict[str, list[float]]


class LedgerRead(NamedTuple):
    prs: list[LedgerPr]
    pr_count: int
    excluded: dict[str, int]
    after_merge_rounds: dict[str, int]


class _PricedRounds(NamedTuple):
    """A PR's rounds less the after-merge ones, with their dollars and the after-merge count per skill."""
    rounds_by_skill: dict[str, list[dict]]
    usd_by_skill: dict[str, list[float]]
    after_merge_rounds: dict[str, int]


def _round_is_after_merge(review_round: dict) -> bool:
    return review_round.get("after_merge") == 1


def _round_usd(review_round: dict) -> float | None:
    """The round's main and agent token splits re-priced at the current rate table, or None when either is
    unpriceable."""
    main_usd = pricing._reprice_token_splits(review_round["main_tokens"])
    agent_usd = pricing._reprice_token_splits(review_round["agent_tokens"])
    if main_usd is None or agent_usd is None:
        return None
    return main_usd + agent_usd


def _price_rounds_before_merge(rounds_by_skill: dict[str, list[dict]]) -> _PricedRounds | None:
    """Removes the after-merge rounds and prices the rest in one pass, or returns None when a round that is not
    after-merge cannot be priced."""
    kept_rounds_by_skill: dict[str, list[dict]] = {}
    usd_by_skill: dict[str, list[float]] = {}
    after_merge_rounds: dict[str, int] = {}
    for skill, rounds in rounds_by_skill.items():
        kept_rounds_by_skill[skill] = []
        usd_by_skill[skill] = []
        for review_round in rounds:
            if _round_is_after_merge(review_round):
                after_merge_rounds[skill] = after_merge_rounds.get(skill, 0) + 1
                continue
            round_usd = _round_usd(review_round)
            if round_usd is None:
                return None
            kept_rounds_by_skill[skill].append(review_round)
            usd_by_skill[skill].append(round_usd)
    return _PricedRounds(kept_rounds_by_skill, usd_by_skill, after_merge_rounds)


def _row_exclusion_reason(cell_rows: list[dict]) -> str | None:
    """The first EXCLUSION_REASONS label, short of the unpriceable-round one, that the PR's cell-carrying rows meet."""
    if not cell_rows:
        return EXCLUDED_NO_CELL
    if len(cell_rows) > 1:
        return EXCLUDED_MULTI_ROW
    row = cell_rows[0]
    if row["join_confidence"] == pr_cost_ledger._PR_COST_JOIN_CONFIDENCE_LOW:
        return EXCLUDED_JOIN_CONFIDENCE_LOW
    if row["review_rounds"]["rule"] != review_rounds.REVIEW_ROUND_RULE:
        return EXCLUDED_RULE
    return None


def _current_rows_for_repo(rows_by_account: Sequence[Sequence[dict]], host: str, repo: str) -> list[dict]:
    """Each account's current rows for `host` and `repo`. The collapse runs per account, so the same PR and machine
    in two accounts stays two rows instead of merging into one."""
    return [
        row
        for account_rows in rows_by_account
        for row, _correction_count in pr_cost_ledger._collapse_pr_cost_rows_to_current(account_rows)
        if row["host"] == host and row["repo"] == repo
    ]


def select_ledger_prs(rows_by_account: Sequence[Sequence[dict]], host: str, repo: str) -> LedgerRead:
    """The PRs whose single current row can be trusted for `host` and `repo`, with the rest counted by reason.

    A degraded status excludes nothing, because a row's size and rounds do not come from the enrichment call that
    status reports on. A round with no after_merge flag stays, because the writer omits the flag only when it
    cannot place the round against the merge.
    """
    rows_by_pr: dict[int, list[dict]] = {}
    for row in _current_rows_for_repo(rows_by_account, host, repo):
        rows_by_pr.setdefault(row["pr_number"], []).append(row)

    excluded = dict.fromkeys(EXCLUSION_REASONS, 0)
    after_merge_rounds: dict[str, int] = {}
    prs: list[LedgerPr] = []
    for pr_number in sorted(rows_by_pr):
        cell_rows = [row for row in rows_by_pr[pr_number] if row["review_rounds"] is not None]
        reason = _row_exclusion_reason(cell_rows)
        if reason is not None:
            excluded[reason] += 1
            continue
        row = cell_rows[0]
        priced_rounds = _price_rounds_before_merge(row["review_rounds"]["rounds"])
        if priced_rounds is None:
            excluded[EXCLUDED_UNPRICEABLE_ROUND] += 1
            continue
        for skill, removed_count in priced_rounds.after_merge_rounds.items():
            after_merge_rounds[skill] = after_merge_rounds.get(skill, 0) + removed_count
        prs.append(LedgerPr(
            pr_number, row["additions"] + row["deletions"], priced_rounds.rounds_by_skill, priced_rounds.usd_by_skill,
        ))
    return LedgerRead(prs, len(rows_by_pr), excluded, after_merge_rounds)


def _read_account_ledger_rows(account_root: Path, ordinal: int, subcommand: str) -> list[dict]:
    """One account's ledger rows, or none when it has no ledger file. Every failure exits 1 and echoes no path or
    cell value.
    Each exit follows its except block so that the caught error does not stay on SystemExit.__context__."""
    ledger_path = pr_cost_ledger._pr_cost_ledger_path_or_exit(subcommand, ordinal, account_root.parent)
    try:
        ledger_text = ledger_path.read_text()
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError):
        ledger_text = None
    if ledger_text is None:
        print(f"{subcommand}: account-{ordinal}: ledger file could not be read", file=sys.stderr)
        sys.exit(1)
    try:
        return pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_text)
    except pr_cost_ledger._PrCostLedgerParseError as exc:
        parse_error_message = str(exc)
    # § "Data" holds the data-row recovery only; a header failure names its own recovery or none.
    data_pointer = ' -- see docs/pr-cost.md § "Data"' if parse_error_message.startswith("line ") else ""
    print(f"{subcommand}: account-{ordinal}: {parse_error_message}{data_pointer}", file=sys.stderr)
    sys.exit(1)


def read_ledger_prs(account_roots: Sequence[Path], host: str, repo: str, *, subcommand: str) -> LedgerRead:
    """select_ledger_prs over each account root's ledger, which lives beside the root's `projects` directory.

    A non-empty PR_COST_LEDGER_PATH replaces every account's ledger path with one file, so it is honored for one root and
    refused for several.
    """
    if len(account_roots) > 1 and pr_cost_ledger.pr_cost_ledger_path_overridden():
        print(
            f"{subcommand}: {pr_cost_ledger._PR_COST_LEDGER_PATH_ENV_VAR} is refused when more than one root resolves"
            " -- unset it (each account then reads its own ledger) or drop --all-accounts",
            file=sys.stderr,
        )
        sys.exit(2)
    ordinals = scope._redaction_ordinals(account_roots)
    root_by_resolved = {root.resolve(): root for root in account_roots}
    rows_by_account = [
        _read_account_ledger_rows(root_by_resolved[resolved_root], ordinals[resolved_root], subcommand)
        for resolved_root in sorted(ordinals)
    ]
    return select_ledger_prs(rows_by_account, host, repo)
