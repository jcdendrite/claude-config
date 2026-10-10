"""The pr-cost command family: cmd_pr_cost and every helper used only by it -- the branch-to-merged-PR join, per-PR gh
enrichment, mechanical review-surface proxies, and the read/--record report.

Every stdout/stderr path routes branch and repo values through redaction._assign_root_scoped_redact_label, never raw.

Imports its package dependencies by module (attribute access, not by name) -- see scope.py's own top-of-file comment for
why."""
from __future__ import annotations

import argparse
import fcntl
import fnmatch
import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath

import _config
from transcript_analysis import corpus, cost, gh_cli, ledger_common, pr_cost_ledger, pricing, redaction, render, scope

# Provisional placeholder ("As-of rule"): a merged PR's branch keeps
# accruing local transcript activity for a while after merge, so
# capturing too early understates its cost. A future measurement pass is
# expected to replace this default with a percentile of (last priced turn
# - mergedAt) across the surviving corpus; 3 days is a defensible guess
# pending that measurement, not a validated figure -- see docs/pr-cost.md.
_PR_COST_ASOF_WINDOW_DAYS_DEFAULT = 3.0

_DEFAULT_PR_COST_PLAN_FILE_GLOB = ".claude/plans/*.md"

# Provisional default risk-surface globs for claude-config itself (paths
# whose review stakes are higher than an average file change: hooks gate
# operations, install scripts run with the operator's own shell, CI
# workflows run with repo secrets, and permission rules govern what future
# agents can do). Not empirically validated against this repo's own
# incident history -- overridable via --risk-surface-glob for another repo.
_DEFAULT_PR_COST_RISK_SURFACE_GLOBS: tuple[str, ...] = (
    "claude/.claude/hooks/**",
    "claude/.claude/settings*.json",
    ".github/workflows/**",
    "install*.sh",
    "claude/.claude/rules/**",
)

# Best-effort, ecosystem-generic test-file heuristic (a tests/ path segment,
# a test_/_test.py Python name, or a .test./.spec. JS/TS suffix) -- not
# claude-config-specific, unlike the risk-surface globs above.
_PR_COST_TEST_FILE_RE = re.compile(
    r"(^|/)tests?/|(^|/)test_[^/]+\.py$|_test\.py$|\.test\.[jt]sx?$|\.spec\.[jt]sx?$"
)


_GIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")


def _gh_pr_view_enrichment(corpus_host: str, pinned_repo: str, pr_number: int) -> tuple[dict | None, str]:
    """Per-PR enrichment call: commits/reviews/files, none of which
    `gh pr list` returns. Returns (payload, pr_cost_ledger._PR_COST_STATUS_OK) on success,
    else (None, degraded) with degraded one of gh_cli._gh_call_with_backoff's own
    status strings -- the caller folds gh_cli._GH_CALL_DEGRADED_AUTH and
    gh_cli._GH_CALL_DEGRADED_HOST_MISMATCH into pr_cost_ledger._PR_COST_STATUS_DEGRADED_NETWORK
    before either reaches a ledger row's status column. `--repo` is
    host-qualified (see gh_cli._gh_host_qualified_repo) so a GHE-pinned repo is
    queried on its own host rather than on api.github.com.
    """
    argv = [
        "gh", "pr", "view", str(pr_number),
        "--repo", gh_cli._gh_host_qualified_repo(corpus_host, pinned_repo), "--json", "commits,reviews,files",
    ]
    proc, degraded = gh_cli._gh_call_with_backoff(argv, label=f"pr view {pr_number}")
    if degraded:
        return None, degraded
    try:
        return json.loads(proc.stdout or "{}"), pr_cost_ledger._PR_COST_STATUS_OK
    except json.JSONDecodeError:
        return None, pr_cost_ledger._PR_COST_STATUS_DEGRADED_NETWORK


def _local_git_object_exists_batch(shas: Sequence[str]) -> set[str]:
    """Which of `shas` resolve to a real local git commit object, checked
    via one `git cat-file --batch-check` call fed the whole list over
    stdin -- avoids one subprocess per SHA. Non-hex-shaped entries are
    dropped before the call: SHAs come from gh's own JSON (commits[].oid),
    and while they're git-generated (hex digits can't start with "-", so
    they're inherently safe in an option position), a malformed API response
    feeding a non-SHA line into the batch-check stdin stream could desync
    this function's own line-based output parsing below.
    """
    valid_shas = [s for s in shas if _GIT_SHA_RE.match(s)]
    if not valid_shas:
        return set()
    try:
        proc = subprocess.run(
            ["git", "cat-file", "--batch-check=%(objectname) %(objecttype)"],
            input="\n".join(valid_shas) + "\n",
            capture_output=True, text=True, timeout=gh_cli._GIT_REMOTE_ORIGIN_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    found: set[str] = set()
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == "commit":
            found.add(parts[0])
    return found


def _pr_cost_sha_overlap(commits_payload) -> int:
    """Count of a PR's pre-squash commit SHAs (gh pr view's own `commits`
    field) that still resolve to a real local git object. GitHub's squash
    merges leave these SHAs unreachable from any live ref once the source
    branch is deleted, but a commit fetched into this clone during the work
    itself can survive as a dangling object until git gc reaps it, giving
    the branch-to-PR join a corroboration signal independent of headRefName
    even after the source branch ref is gone.
    """
    shas = [c.get("oid", "") for c in (commits_payload or []) if isinstance(c, dict)]
    return len(_local_git_object_exists_batch(shas))


def _pr_cost_plan_slug_from_files(file_paths: Sequence[str], plan_glob: str) -> str | None:
    """The added plan file's slug (filename minus extension) when a PR's
    changed-file list matches plan_glob exactly once. Measured against this
    repo's own recent PR history: the overwhelming majority of in-window PRs
    add exactly one such file and none add more than one, so more than one
    match is treated as no usable slug rather than guessed.
    """
    matches = [p for p in file_paths if fnmatch.fnmatch(p, plan_glob)]
    if len(matches) != 1:
        return None
    return PurePosixPath(matches[0]).stem


def _direct_headref_matches(branch: str, merged_prs: Sequence[dict]) -> list[dict]:
    """Every merged PR whose own headRefName equals `branch` -- gh's
    headRefName is this join's authoritative signal. Normally at most one;
    more than one means a branch name was reused across two merged PRs,
    resolved by _resolve_branch_pr.
    """
    return [pr for pr in merged_prs if pr.get("headRefName") == branch]


def _pr_cost_join_corroborated(branch: str, enrichment: dict | None, plan_glob: str) -> bool:
    """True when either independent cross-check corroborates a direct
    headRefName match: the PR's own added plan-file slug equals `branch`, or
    at least one of its pre-squash commit SHAs still resolves locally
    (_pr_cost_sha_overlap). False when enrichment itself could not be
    fetched -- there is no data to corroborate with.
    """
    if enrichment is None:
        return False
    files = [f.get("path", "") for f in (enrichment.get("files") or []) if isinstance(f, dict)]
    if _pr_cost_plan_slug_from_files(files, plan_glob) == branch:
        return True
    return _pr_cost_sha_overlap(enrichment.get("commits")) > 0


def _resolve_branch_pr(
    branch: str, matches: Sequence[dict], enrichment_by_pr_number: dict[int, dict], plan_glob: str,
) -> tuple[dict | None, str]:
    """Join one branch already known to have >=1 direct headRefName match in
    `matches` (see _direct_headref_matches) to the merged PR it belongs to.
    A single match is "high" confidence when either cross-check corroborates
    it, else "medium" -- gh's headRefName is authoritative either way;
    corroboration only grades confidence. More than one match means this
    branch name was reused across two merged PRs: highest SHA overlap wins,
    ties broken by most recent mergedAt; a remaining tie returns no resolved
    PR with join_confidence "low". Rename/alias detection for a branch with
    *no* direct match at all is a separate, manual audit step, not
    automated here.
    """
    if len(matches) == 1:
        pr = matches[0]
        corroborated = _pr_cost_join_corroborated(branch, enrichment_by_pr_number.get(pr["number"]), plan_glob)
        return pr, (
            pr_cost_ledger._PR_COST_JOIN_CONFIDENCE_HIGH if corroborated else pr_cost_ledger._PR_COST_JOIN_CONFIDENCE_MEDIUM
        )

    def _overlap(pr: dict) -> int:
        enrichment = enrichment_by_pr_number.get(pr["number"])
        return _pr_cost_sha_overlap(enrichment.get("commits") if enrichment else None)

    best = max(matches, key=lambda pr: (_overlap(pr), pr["mergedAt"]))
    tied = [pr for pr in matches if (_overlap(pr), pr["mergedAt"]) == (_overlap(best), best["mergedAt"])]
    if len(tied) > 1:
        return None, pr_cost_ledger._PR_COST_JOIN_CONFIDENCE_LOW
    return best, pr_cost_ledger._PR_COST_JOIN_CONFIDENCE_LOW


def _top_level_dir(path: str) -> str:
    """First path segment of a changed-file path, or the bare filename when
    it has none (a repo-root file counts as its own single-item bucket)."""
    return path.split("/", 1)[0]


def _pr_cost_mechanical_proxies(file_paths: Sequence[str], *, plan_glob: str, risk_globs: Sequence[str]) -> dict:
    """Mechanical review-surface proxies, computed once from one PR's
    changed-file path list (gh pr view --json files)."""
    return {
        "distinct_top_level_dirs": len({_top_level_dir(p) for p in file_paths}),
        "distinct_file_extensions": len({PurePosixPath(p).suffix for p in file_paths}),
        "tests_changed": any(_PR_COST_TEST_FILE_RE.search(p) for p in file_paths),
        "plan_file_added": any(fnmatch.fnmatch(p, plan_glob) for p in file_paths),
        "risk_surface_flag": any(fnmatch.fnmatch(p, glob) for p in file_paths for glob in risk_globs),
    }


def _pr_cost_asof_window_ok(merged_at_iso: str, window_days: float, now: datetime) -> bool:
    """True once at least window_days have elapsed since merged_at_iso --
    the as-of rule's precondition."""
    merged_ts = corpus._parse_ts(merged_at_iso)
    if merged_ts is None:
        return False
    return now.timestamp() - merged_ts >= window_days * 86400


def _usd_to_micros(dollars: float) -> int:
    """Integer micro-dollars the ledger's own six-decimal rendering of `dollars` denotes."""
    return int(Decimal(f"{dollars:.6f}").scaleb(6))


def _build_model_breakdown_cell(by_model: dict) -> dict:
    """The model_breakdown cell value for one branch's cost._new_pr_cost_agg "by_model": each leaf's
    float dollars converted once to integer micro-dollars."""
    return {
        model: {
            variant: {
                token_class: {"tokens": leaf["tokens"], "usd_micros": _usd_to_micros(leaf["dollars"])}
                for token_class, leaf in leaves_by_class.items()
            }
            for variant, leaves_by_class in leaves_by_variant.items()
        }
        for model, leaves_by_variant in by_model.items()
    }


# The closed set of rules _check_model_breakdown_cell can fail on, in check order, each with the cause its per-PR line prints.
# Transcript data reaches "shape" through a negative token count, and "dollars" through token counts so large that float
# summation order alone moves a class total by more than the rounding tolerance. The other rules are claude-config defects.
_MODEL_BREAKDOWN_CHECK_CAUSES: dict[str, str] = {
    "shape": "malformed transcript data or a claude-config defect",
    "model-membership": "a claude-config defect",
    "label-membership": "a claude-config defect",
    "tokens": "a claude-config defect",
    "dollars": "malformed transcript data or a claude-config defect",
}
_MODEL_BREAKDOWN_CHECK_RULES: tuple[str, ...] = tuple(_MODEL_BREAKDOWN_CHECK_CAUSES)


class _ModelBreakdownCheckError(Exception):
    """A model_breakdown cell failed one _MODEL_BREAKDOWN_CHECK_RULES check. The exception's text is the
    rule alone, never a key or value. An unknown rule raises KeyError here, inside the check and before any write."""

    def __init__(self, rule: str) -> None:
        super().__init__(rule)
        self.rule = rule
        self.cause = _MODEL_BREAKDOWN_CHECK_CAUSES[rule]


def _check_model_breakdown_cell(cell: dict | None, row: dict) -> None:
    """Raises _ModelBreakdownCheckError on the first failing rule, so the tool never writes a cell it would
    refuse to read or one that disagrees with the row's own per-class scalars. A None cell (nothing recorded)
    skips every check."""
    if cell is None:
        return

    try:
        decoded_back = pr_cost_ledger._decode_model_breakdown_cell(pr_cost_ledger._encode_model_breakdown_cell(cell), 0)
        is_shape_valid = decoded_back == cell
    except pr_cost_ledger._PrCostLedgerParseError:
        is_shape_valid = False
    if not is_shape_valid:
        raise _ModelBreakdownCheckError("shape")

    if any(model not in pricing._MODEL_BASE_INPUT_RATES for model in cell):
        raise _ModelBreakdownCheckError("model-membership")

    groups = [leaves_by_class for leaves_by_variant in cell.values() for leaves_by_class in leaves_by_variant.values()]
    if any(variant not in pricing._PRICING_VARIANTS for leaves_by_variant in cell.values() for variant in leaves_by_variant):
        raise _ModelBreakdownCheckError("label-membership")
    if any(set(leaves_by_class) != set(pricing._TOKEN_CLASSES) for leaves_by_class in groups):
        raise _ModelBreakdownCheckError("label-membership")

    if any(
        sum(leaves_by_class[token_class]["tokens"] for leaves_by_class in groups) != row[f"{token_class}_tokens"]
        for token_class in pricing._TOKEN_CLASSES
    ):
        raise _ModelBreakdownCheckError("tokens")

    # N leaves and the scalar each round by at most half a micro-dollar, so while their float sums agree
    # their integer gap is at most (N + 1) // 2.
    group_count = len(groups)
    tolerance_micros = 0 if group_count <= 1 else (group_count + 1) // 2
    if any(
        abs(
            sum(leaves_by_class[token_class]["usd_micros"] for leaves_by_class in groups)
            - _usd_to_micros(row[f"{token_class}_usd"])
        )
        > tolerance_micros
        for token_class in pricing._TOKEN_CLASSES
    ):
        raise _ModelBreakdownCheckError("dollars")


def _new_pr_cost_row(
    *, host: str, pinned_repo: str, pr: dict, branch: str, agg: dict, enrichment: dict | None,
    join_confidence: str, status: str, machine: str, captured_at: str, supersedes: str,
    plan_glob: str, risk_globs: Sequence[str], ordinal: int, branch_map: dict,
) -> dict:
    """Assemble one ledger row dict from every piece the main loop resolved.
    head_branch is the SCRUBBED form (_assign_root_scoped_redact_label) --
    the join itself already ran on the raw `branch` value passed in here;
    this is the write boundary, the only point this run's branch value is
    allowed to reach the ledger or stdout/stderr. `host` and `repo` are
    stored raw: both are part of the row's own key and must stay stable and
    comparable across runs for the ledger to function at all (PR numbers are
    only unique per-(host, repo)) -- every *print* of `repo` still routes
    through the caller's own repo_map instead.
    """
    dollars = agg["dollars"]
    tokens = agg["tokens"]
    turn_count = agg["turn_count"]
    total_dollars = sum(dollars.values())

    files = [f.get("path", "") for f in ((enrichment or {}).get("files") or []) if isinstance(f, dict)]
    proxies = _pr_cost_mechanical_proxies(files, plan_glob=plan_glob, risk_globs=risk_globs)
    reviews = (enrichment or {}).get("reviews") or []
    commits = (enrichment or {}).get("commits") or []

    return {
        "host": host,
        "repo": pinned_repo,
        "pr_number": pr["number"],
        "machine": machine,
        "head_branch": redaction._assign_root_scoped_redact_label("branch", ordinal, branch, branch_map),
        "merged_at": pr["mergedAt"],
        "rate_stamp": pricing._PRICING_FETCH_DATE.isoformat(),
        "captured_at": captured_at,
        "join_confidence": join_confidence,
        "supersedes": supersedes,
        "status": status,
        "cache_read_usd": dollars["cache_read"], "cache_write_5m_usd": dollars["cache_write_5m"],
        "cache_write_1h_usd": dollars["cache_write_1h"], "output_usd": dollars["output"],
        "input_usd": dollars["input"],
        "cache_read_tokens": tokens["cache_read"], "cache_write_5m_tokens": tokens["cache_write_5m"],
        "cache_write_1h_tokens": tokens["cache_write_1h"], "output_tokens": tokens["output"],
        "input_tokens": tokens["input"],
        "unpriced_turns": agg["unpriced_turns"], "unpriced_tokens": agg["unpriced_tokens"],
        "turn_count": turn_count, "session_count": len(agg["sessions"]),
        "opus_dollars": agg["opus_dollars"], "opus_dollar_share_pct": render._pct_value(agg["opus_dollars"], total_dollars),
        "sum_context_at_turn": agg["sum_context_at_turn"],
        "mean_context_at_turn": (agg["sum_context_at_turn"] / turn_count) if turn_count else 0.0,
        "additions": pr.get("additions", 0), "deletions": pr.get("deletions", 0),
        "changed_files": pr.get("changedFiles", 0),
        "commit_count": len(commits), "review_comment_count": len(reviews),
        "distinct_top_level_dirs": proxies["distinct_top_level_dirs"],
        "distinct_file_extensions": proxies["distinct_file_extensions"],
        "tests_changed": proxies["tests_changed"], "plan_file_added": proxies["plan_file_added"],
        "risk_surface_flag": proxies["risk_surface_flag"],
        "model_breakdown": _build_model_breakdown_cell(agg["by_model"]),
    }


def _print_pr_cost_ledger_rows(rows: list[dict], ordinal: int, branch_map: dict, repo_map: dict) -> None:
    """Read-mode's existing-rows preview -- scrubbed repo, no branch column
    at all (head_branch is already the scrubbed placeholder stored in the
    row, so re-scrubbing it through `branch_map` would double-redact it)."""
    if not rows:
        print("\nNo rows recorded yet.")
        return
    print()
    print(f"{'Repo':<28} {'PR':>6} {'Machine':<9} {'Status':<20} {'Join':<8} {'CapturedAt':<20}")
    for row in rows:
        repo_label = redaction._assign_root_scoped_redact_label("repo", ordinal, row["repo"], repo_map)
        print(
            f"{repo_label:<28} {row['pr_number']:>6} {row['machine']:<9} {row['status']:<20}"
            f" {row['join_confidence']:<8} {row['captured_at']:<20}"
        )


def _print_pr_cost_uncaptured(
    branch_totals: dict[str, dict], merged_prs: Sequence[dict], existing_rows: list[dict],
    corpus_host: str, pinned_repo: str, machine_label: str | None, ordinal: int, branch_map: dict,
) -> None:
    """Read mode's gap listing: merged PRs with local corpus activity not
    yet captured in the ledger. Restricted to an unambiguous direct
    headRefName match (a branch with zero or more-than-one match is a
    separate manual audit's territory, not this quick gap check) --
    deliberately makes no extra gh calls beyond the bulk discovery this run
    already made, so read mode stays cheap enough to run often, closing the
    capture-trigger gap without needing a hook.
    """
    print("\nMerged PRs with local corpus activity not yet captured:")
    any_uncaptured = False
    for branch in sorted(branch_totals):
        matches = _direct_headref_matches(branch, merged_prs)
        if len(matches) != 1:
            continue
        pr = matches[0]
        if pr_cost_ledger._latest_pr_cost_row(existing_rows, corpus_host, pinned_repo, pr["number"], machine_label) is not None:
            continue
        any_uncaptured = True
        label = redaction._assign_root_scoped_redact_label("branch", ordinal, branch, branch_map)
        print(f"  PR #{pr['number']:<6} {label:<40} merged {pr['mergedAt']}")
    if not any_uncaptured:
        print("  (none)")


def cmd_pr_cost(args: argparse.Namespace) -> None:
    """CLI entry point for the pr-cost subcommand.

    Reads the wall-clock date/time exactly once, here, mirroring cost's and
    cost-ledger's own today-injection split so the as-of window's
    precondition check is deterministic under test.
    """
    roots = scope._resolve_cost_roots(args, "pr-cost")
    _pr_cost_report(args, datetime.now(UTC), roots)


def _pr_cost_report(args: argparse.Namespace, now: datetime, roots: Sequence[Path]) -> None:
    """Read (default) or capture (--record) pr-cost ledger rows, one full
    report per resolved account.

    Failure-handling order: gh auth preflight, then repo-identity resolution
    (retried under the shared rate-limit backoff, aborting the whole run on
    exhaustion since no row exists yet to mark degraded), then discovery --
    each resolved once for the whole run, since gh auth/identity and merged-PR
    discovery are account-independent (never scoped by CLAUDE_CONFIG_DIR).
    Everything else -- local corpus scan, per-branch enrichment (rate-limit/
    network failures degrade that branch's row instead of aborting), and the
    ledger read/print/write -- loops once per resolved root. A --record row
    whose model_breakdown fails _check_model_breakdown_cell is still written,
    with an empty cell; after that row's write the loop prints a per-PR line,
    a ledger rewritten from an older header prints one upgrade notice, and a
    run that finished every branch with such a row prints a count line and
    exits 1. A refused write exits 1 at once and suppresses that row's per-PR
    line, any upgrade notice, and the count line; earlier branches' lines have
    already printed.
    Every stdout/stderr path below routes branch/repo values through
    _assign_root_scoped_redact_label -- no raw branch name or repo value is
    ever printed. There is deliberately no --no-redact escape hatch for this
    subcommand, unlike cost/subagents.
    """
    all_accounts: bool = bool(getattr(args, "all_accounts", False))
    if len(roots) > 1 and not all_accounts:
        # Refuses genuine multi-root ambiguity only, not a claude-config-only
        # scope requirement (this subcommand is never restricted to running
        # against claude-config itself) -- load-bearing here because pr-cost
        # durably writes, unlike a pure read command, and even read mode
        # could otherwise conflate two accounts' branch/repo data into one
        # listing.
        print(
            "pr-cost: more than one root resolved -- refusing a durable write (or a read that"
            " could conflate two accounts' branch/repo data) across accounts; pass --all-accounts"
            " to scan every declared account in one run (each account's own opt-in sentinel still"
            " gates its own write), or scope to a single profile (drop --config-dir)",
            file=sys.stderr,
        )
        sys.exit(2)
    if all_accounts and len(roots) > 1 and os.environ.get("PR_COST_LEDGER_PATH"):
        # A single forced path would commingle every account's rows into one
        # file, defeating the per-account separation the sentinel gate below
        # depends on.
        print(
            "pr-cost: PR_COST_LEDGER_PATH is refused with --all-accounts across more than one"
            " resolved root -- unset PR_COST_LEDGER_PATH (each account then defaults to its own"
            " ledger path) or drop --all-accounts",
            file=sys.stderr,
        )
        sys.exit(2)

    record: bool = bool(getattr(args, "record", False))
    force: bool = bool(getattr(args, "force", False))
    target_pr: int | None = getattr(args, "pr", None)
    machine_label: str | None = getattr(args, "machine_label", None) or None
    window_days: float = getattr(args, "asof_window_days", None) or _PR_COST_ASOF_WINDOW_DAYS_DEFAULT
    plan_glob: str = getattr(args, "plan_file_glob", None) or _DEFAULT_PR_COST_PLAN_FILE_GLOB
    risk_globs: tuple[str, ...] = tuple(
        getattr(args, "risk_surface_globs", None) or _DEFAULT_PR_COST_RISK_SURFACE_GLOBS
    )

    if force and target_pr is None:
        print("pr-cost: --force requires --pr (a correction targets exactly one PR)", file=sys.stderr)
        sys.exit(1)
    # Precedes the format check below, because --record's machine identity
    # is generated, never operator-supplied. Firing first avoids giving a
    # malformed --machine-label a fix-the-format message that would invite
    # retrying with --record still set.
    if record and machine_label is not None:
        print(
            "pr-cost: --machine-label is not accepted with --record -- machine identity is"
            " generated and persisted automatically; see docs/pr-cost.md. --machine-label still"
            " narrows read mode's uncaptured-PR listing to one machine",
            file=sys.stderr,
        )
        sys.exit(1)
    if machine_label is not None and not ledger_common._MACHINE_LABEL_RE.match(machine_label):
        print(f"pr-cost: --machine-label {machine_label!r} must match ^[a-z0-9]{{1,8}}$", file=sys.stderr)
        sys.exit(1)

    corpus_host, corpus_repo = gh_cli._git_remote_origin_host_and_owner_repo()
    if not gh_cli._gh_auth_preflight_ok(corpus_host):
        print("pr-cost: gh auth status failed -- run `gh auth login` before pr-cost", file=sys.stderr)
        sys.exit(1)

    # gh auth/identity and merged-PR discovery are account-independent, so
    # they're resolved once for the whole run rather than once per account
    # below. redact_ordinals is computed first so gh_cli._resolve_pinned_gh_repo's
    # own mismatch-refusal message has an ordinal to label with.
    redact_ordinals = scope._redaction_ordinals(roots)
    pinned_repo, repo_map = gh_cli._resolve_pinned_gh_repo(corpus_host, corpus_repo, ordinal=redact_ordinals[roots[0].resolve()])
    merged_prs = gh_cli._gh_discover_merged_prs(corpus_host, pinned_repo)
    branch_map: dict[tuple[int, str], str] = {}  # shared across accounts; key already includes ordinal

    recorded = skipped_no_sentinel = skipped_other = 0
    rows_without_breakdown: list[str] = []
    for root in roots:
        account_config_dir = root.parent
        ordinal = redact_ordinals[root.resolve()]

        session_iter, scope_label = scope._resolve_project_scope(
            args, "pr-cost", include_subagents=True, roots=[root]
        )
        scope.print_resolved_scope("pr-cost", scope_label, [root])
        branch_totals, unbranched_agg = cost._compute_pr_cost_branch_totals(session_iter)
        print(
            f"pr-cost: {render._fmt_usd(sum(unbranched_agg['dollars'].values()))} across"
            f" {unbranched_agg['turn_count']} priced turns attributed to no branch at all"
            " (counted, not skipped, unlike `buckets`)",
            file=sys.stderr,
        )

        try:
            ledger_path = pr_cost_ledger._pr_cost_ledger_path(config_dir_override=account_config_dir)
        except ValueError:
            # Not str(exc): pr_cost_ledger._pr_cost_ledger_path's own message embeds
            # PR_COST_LEDGER_PATH's raw value, which can carry a home-rooted
            # engagement path. Same discipline as pr-cost-export's identical
            # catch.
            print(f"pr-cost: account-{ordinal}: PR_COST_LEDGER_PATH must be an absolute path", file=sys.stderr)
            sys.exit(1)

        if not record:
            existing_rows: list[dict] = []
            if ledger_path.exists():
                try:
                    existing_rows = pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
                except pr_cost_ledger._PrCostLedgerParseError as exc:
                    print(f"pr-cost: {exc}", file=sys.stderr)
                    sys.exit(1)
            _print_pr_cost_ledger_rows(existing_rows, ordinal, branch_map, repo_map)
            _print_pr_cost_uncaptured(
                branch_totals, merged_prs, existing_rows, corpus_host, pinned_repo, machine_label, ordinal, branch_map
            )
            continue

        try:
            pr_cost_recording_enabled = _config.config_enabled(
                "pr_cost_recording", config_dir_override=account_config_dir
            )
        except _config.ConfigSchemaEmptyError:
            # config-keys.psv was read successfully but produced zero
            # schema rows (see that class's own docstring) -- distinct
            # from the unreadable-file case below, which this file was
            # not.
            print(
                "pr-cost: --record found config-keys.psv empty or malformed"
                " (no parseable schema rows) -- see docs/pr-cost.md",
                file=sys.stderr,
            )
            sys.exit(1)
        except _config.ConfigSchemaRowTruncatedError:
            # pr_cost_recording's own row is present but truncated after an
            # earlier column (see that class's own docstring) -- a torn
            # schema row, not a renamed or typo'd key literal at this call
            # site.
            print(
                "pr-cost: --record found pr_cost_recording's config-keys.psv"
                " row truncated (partial stow-relink or interrupted git pull)"
                " -- see docs/pr-cost.md",
                file=sys.stderr,
            )
            sys.exit(1)
        except KeyError as exc:
            if _config.schema():
                # config-keys.psv parsed fine (schema() returned rows), so
                # this KeyError is a real unknown-key bug -- a renamed or
                # typo'd literal at this call site -- not the infrastructure
                # cause the message below assumes.
                print(f"pr-cost: --record: unknown config key {exc}", file=sys.stderr)
                sys.exit(1)
            # config-keys.psv itself was unreadable at the moment of this
            # call (see _config.py's module docstring) -- a partial
            # stow-relink or interrupted `git pull`, not a caller-side
            # key-name typo.
            print(
                "pr-cost: --record could not read config-keys.psv (partial stow-relink"
                " or interrupted git pull) -- see docs/pr-cost.md",
                file=sys.stderr,
            )
            sys.exit(1)
        if pr_cost_recording_enabled is None:
            # account_config_dir is always a concrete, already-resolved
            # directory here (root.parent) -- not expected to be reachable
            # in practice -- handled explicitly anyway so a future
            # account_config_dir computation change fails loud rather than
            # silently misreporting "not opted in".
            if all_accounts:
                print(
                    f"pr-cost: account-{ordinal}'s config directory could not be resolved --"
                    " skipped, see docs/pr-cost.md",
                    file=sys.stderr,
                )
                skipped_other += 1
                continue
            print(
                "pr-cost: --record could not resolve the Claude Code config directory --"
                " see docs/pr-cost.md",
                file=sys.stderr,
            )
            sys.exit(1)
        if not pr_cost_recording_enabled:
            if all_accounts:
                # account-N, not the resolved config dir, to avoid a
                # resolved home-rooted path in output -- same discipline as
                # the single-account refusal message below. Worded
                # generically ("not opted in"), not as a missing-sentinel-
                # file claim. Same rationale as pr-cost-export's identical
                # message: an explicit pr_cost_recording = false in
                # claude-config.toml reaches this branch with no sentinel
                # file involved at all.
                print(
                    f"pr-cost: account-{ordinal} is not opted in (pr_cost_recording) --"
                    " skipped, see docs/pr-cost.md",
                    file=sys.stderr,
                )
                skipped_no_sentinel += 1
                continue
            # Worded generically for the same reason as the all_accounts
            # branch above.
            print(
                "pr-cost: --record is not opted in (pr_cost_recording) --"
                " see docs/pr-cost.md",
                file=sys.stderr,
            )
            sys.exit(1)
        if ledger_common._ledger_path_is_git_tracked(ledger_path, "pr-cost"):
            # Always refused for pr-cost (not gated on multi-root, unlike the
            # weekly ledger's own check): these rows carry branch/repo data
            # the public weekly ledger's rows don't, so this ledger must
            # never live inside a git working tree, full stop.
            if all_accounts:
                print(
                    f"pr-cost: account-{ordinal}'s ledger path is inside a git working tree --"
                    " skipped, see docs/pr-cost.md",
                    file=sys.stderr,
                )
                skipped_other += 1
                continue
            print(
                "pr-cost: --record is refused when the ledger path is inside a git working tree --"
                " move PR_COST_LEDGER_PATH outside git, or drop --record",
                file=sys.stderr,
            )
            sys.exit(2)

        # Resolved only after the opt-in gate and the git-tracked refusal
        # above, so a run never creates a machine-id file for an account
        # that never opted in to --record.
        # The refusal message names the account as account-N, not a resolved
        # path, because --all-accounts means account_config_dir isn't the
        # default ~/.claude the un-labeled convention would otherwise imply.
        machine_identity = ledger_common._resolve_machine_identity(
            "pr-cost",
            config_dir_override=account_config_dir,
            location_label=f"account-{ordinal}" if all_accounts else None,
        )

        # Read under this account's own write lock, immediately before the
        # warn call — matching _cost_ledger_report's own read-under-lock
        # symmetry. A read taken outside any lock could warn from a state a
        # concurrent writer has already superseded.
        # Known, accepted residual: the ledger write happens in a later,
        # separate per-branch lock, so a concurrent writer landing between
        # this lock's release and that lock's acquire still sees a stale
        # advisory warning.
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = ledger_path.with_name(ledger_path.name + ".lock")
        with open(lock_path, "w") as lock_f:
            pr_cost_ledger._acquire_pr_cost_ledger_lock(lock_f)
            try:
                try:
                    existing_rows = pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
                except FileNotFoundError:
                    existing_rows = []
                except pr_cost_ledger._PrCostLedgerParseError as exc:
                    print(f"pr-cost: {exc}", file=sys.stderr)
                    sys.exit(1)
                ledger_common._warn_machine_identity_absent_from_ledger("pr-cost", machine_identity, existing_rows)
            finally:
                fcntl.flock(lock_f, fcntl.LOCK_UN)

        if target_pr is not None:
            pr_by_number = {pr["number"]: pr for pr in merged_prs}
            target_pr_data = pr_by_number.get(target_pr)
            if target_pr_data is None:
                print(f"pr-cost: PR #{target_pr} was not found among this repo's merged PRs", file=sys.stderr)
                sys.exit(1)
            target_branches = [target_pr_data["headRefName"]]
        else:
            target_branches = sorted(branch_totals)

        account_recorded_a_row = False
        for branch in target_branches:
            branch_label = redaction._assign_root_scoped_redact_label("branch", ordinal, branch, branch_map)
            print(f"pr-cost: resolving branch {branch_label}...", file=sys.stderr)
            matches = _direct_headref_matches(branch, merged_prs)
            if not matches:
                print("pr-cost:   no merged PR found for this branch -- skipped", file=sys.stderr)
                continue

            enrichment_by_pr_number: dict[int, dict] = {}
            degraded_status_by_pr_number: dict[int, str] = {}
            for pr in matches:
                print(f"pr-cost:   enriching PR #{pr['number']}...", file=sys.stderr)
                payload, degraded = _gh_pr_view_enrichment(corpus_host, pinned_repo, pr["number"])
                if payload is not None:
                    enrichment_by_pr_number[pr["number"]] = payload
                else:
                    degraded_status_by_pr_number[pr["number"]] = (
                        pr_cost_ledger._PR_COST_STATUS_DEGRADED_NETWORK
                        if degraded in (gh_cli._GH_CALL_DEGRADED_AUTH, gh_cli._GH_CALL_DEGRADED_HOST_MISMATCH)
                        else degraded
                    )

            resolved_pr, join_confidence = _resolve_branch_pr(branch, matches, enrichment_by_pr_number, plan_glob)
            if resolved_pr is None:
                print(
                    "pr-cost:   ambiguous branch-to-PR match (ties unresolved after SHA-overlap"
                    " and mergedAt comparison) -- skipped",
                    file=sys.stderr,
                )
                continue

            enrichment = enrichment_by_pr_number.get(resolved_pr["number"])
            row_status = pr_cost_ledger._PR_COST_STATUS_OK if enrichment is not None else degraded_status_by_pr_number.get(
                resolved_pr["number"], pr_cost_ledger._PR_COST_STATUS_DEGRADED_NETWORK
            )

            if not _pr_cost_asof_window_ok(resolved_pr["mergedAt"], window_days, now):
                message = (
                    f"pr-cost:   PR #{resolved_pr['number']} merged too recently"
                    f" (as-of window is {window_days:g}d)"
                )
                if target_pr is not None and not all_accounts:
                    print(f"{message} -- refusing", file=sys.stderr)
                    sys.exit(1)
                print(f"{message} -- skipped", file=sys.stderr)
                continue

            ledger_path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = ledger_path.with_name(ledger_path.name + ".lock")
            with open(lock_path, "w") as lock_f:
                pr_cost_ledger._acquire_pr_cost_ledger_lock(lock_f)
                try:
                    try:
                        current_rows = pr_cost_ledger._parse_pr_cost_ledger_file_text(ledger_path.read_text())
                    except FileNotFoundError:
                        current_rows = []
                    except pr_cost_ledger._PrCostLedgerParseError as exc:
                        print(f"pr-cost: {exc}", file=sys.stderr)
                        sys.exit(1)

                    already = pr_cost_ledger._latest_pr_cost_row(
                        current_rows, corpus_host, pinned_repo, resolved_pr["number"], machine_identity
                    )
                    if already is not None and not force:
                        print(
                            f"pr-cost:   PR #{resolved_pr['number']} for machine={machine_identity} is already"
                            " captured -- pass --force (with --pr) to append a correcting row",
                            file=sys.stderr,
                        )
                        if target_pr is not None and not all_accounts:
                            sys.exit(1)
                        continue

                    # A --pr target's branch comes from the shared, repo-wide
                    # merged_prs list, so under --all-accounts it can resolve
                    # here even for an account whose own local corpus never
                    # touched it; skip rather than fall through to a
                    # zero-valued-agg row (single-account --pr N still writes
                    # that row -- there is no other account to fall back to).
                    if all_accounts and target_pr is not None and branch not in branch_totals:
                        print(
                            f"pr-cost:   account-{ordinal} has no local corpus activity for this"
                            " branch -- skipped",
                            file=sys.stderr,
                        )
                        continue

                    if branch not in branch_totals and branch_totals:
                        # branch_totals non-empty but missing this exact key means the account
                        # saw local activity under some other branch name -- distinct from
                        # genuine branch-idle (branch_totals empty), which is a legitimate
                        # zero-cost case that must not warn.
                        print(
                            f"pr-cost:   PR #{resolved_pr['number']}'s branch has no matching"
                            " local corpus activity, but this account's scan attributed activity"
                            f" to {len(branch_totals)} other branch(es) -- this row may"
                            " under-report if the branch was renamed; investigate locally with"
                            " `cost --branches <branch>`",
                            file=sys.stderr,
                        )
                    agg = branch_totals.get(branch) or cost._new_pr_cost_agg()
                    captured_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
                    new_row = _new_pr_cost_row(
                        host=corpus_host, pinned_repo=pinned_repo, pr=resolved_pr, branch=branch, agg=agg,
                        enrichment=enrichment, join_confidence=join_confidence, status=row_status,
                        machine=machine_identity, captured_at=captured_at,
                        supersedes=(already["captured_at"] if already else ""),
                        plan_glob=plan_glob, risk_globs=risk_globs, ordinal=ordinal, branch_map=branch_map,
                    )
                    # A failed check degrades this row to an empty cell instead of costing its scalars or the run's
                    # remaining branches; the loop reports it after the write.
                    failed_breakdown_check: _ModelBreakdownCheckError | None = None
                    try:
                        _check_model_breakdown_cell(new_row["model_breakdown"], new_row)
                    except _ModelBreakdownCheckError as exc:
                        failed_breakdown_check = exc
                        new_row["model_breakdown"] = None
                    try:
                        updated_rows = pr_cost_ledger._append_pr_cost_ledger_row(current_rows, new_row, already, force)
                    except ValueError as exc:
                        print(f"pr-cost: {exc}", file=sys.stderr)
                        sys.exit(1)
                    try:
                        ledger_was_upgraded = pr_cost_ledger._write_pr_cost_ledger_file(ledger_path, updated_rows)
                    except pr_cost_ledger._PrCostLedgerParseError as exc:
                        print(f"pr-cost: {exc}", file=sys.stderr)
                        sys.exit(1)
                finally:
                    fcntl.flock(lock_f, fcntl.LOCK_UN)
            existing_rows = updated_rows
            account_recorded_a_row = True
            print(f"pr-cost: recorded PR #{resolved_pr['number']} / {machine_identity}")
            if ledger_was_upgraded:
                ledger_label = f"account-{ordinal}'s ledger" if all_accounts else "the ledger"
                print(
                    f"pr-cost: upgraded {ledger_label} to the current header -- older claude-config checkouts"
                    " refuse this file until they are updated, and there is no supported downgrade; see"
                    " docs/pr-cost.md in the claude-config repo",
                    file=sys.stderr,
                )
            if failed_breakdown_check is not None:
                print(
                    f"pr-cost:   PR #{resolved_pr['number']}: per-model breakdown failed its {failed_breakdown_check.rule}"
                    f" check ({failed_breakdown_check.cause}) -- recorded the row without it;"
                    " see docs/pr-cost.md",
                    file=sys.stderr,
                )
                rows_without_breakdown.append(
                    f"{f'account-{ordinal} ' if all_accounts else ''}PR #{resolved_pr['number']}"
                )

        if account_recorded_a_row:
            recorded += 1  # counts accounts that wrote a row, not total rows written
        elif all_accounts:
            # Covers every branch-loop skip reason (no PR match, ambiguous
            # match, already captured, asof-window, not-in-corpus) in one
            # place, so an account with zero recorded rows is never absent
            # from all three summary counters below.
            skipped_other += 1

    if record and all_accounts:
        print(
            f"pr-cost: recorded {recorded} of {len(roots)} declared accounts"
            f" ({skipped_no_sentinel} not opted in, {skipped_other} skipped)"
        )
    if rows_without_breakdown:
        print(
            f"pr-cost: {len(rows_without_breakdown)} row(s) recorded without a per-model breakdown"
            f" ({', '.join(rows_without_breakdown)}); those rows are valid -- once the cause is fixed, re-capture each"
            " with --record --force --pr N and this run's account flags, only while every session of that PR is still"
            " inside cleanupPeriodDays (see docs/pr-cost.md)",
            file=sys.stderr,
        )
        sys.exit(1)
