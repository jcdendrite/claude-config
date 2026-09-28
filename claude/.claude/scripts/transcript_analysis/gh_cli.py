"""gh and git-remote access shared by pr-link, pr-cost, and workstream-cost: origin host/owner/repo parsing, gh stderr
classification, rate-limit backoff, auth preflight, effective-repo pinning, and merged/closed PR discovery.

Imports pr_cost_ledger and redaction by module (attribute access, not by name) -- see scope.py's own top-of-file comment for
why."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from collections.abc import Sequence

from transcript_analysis import pr_cost_ledger, redaction

# The gh-call-level outcomes _gh_call_with_backoff itself returns on an
# auth-shaped or local-misconfiguration-shaped failure -- never ledger
# status values; every caller folds them into pr_cost_ledger._PR_COST_STATUS_DEGRADED_NETWORK
# before they reach a row (see pr_cost_ledger.py's status enum).
_GH_CALL_DEGRADED_AUTH = "degraded_auth"
_GH_CALL_DEGRADED_HOST_MISMATCH = "degraded_host_mismatch"


_GH_CALL_TIMEOUT_S = 30.0  # Operational default: gh publishes no single
# per-call timeout recommendation, so this is a considered guess generous
# enough for one REST round trip, not a network SLA citation.
_PR_COST_RATE_LIMIT_MIN_BACKOFF_S = 60.0  # GitHub REST API docs, "Rate
# limits for the REST API" -- secondary-rate-limit guidance: wait at least
# one minute between retries when no Retry-After header is present.
_PR_COST_RATE_LIMIT_MAX_ATTEMPTS = 5  # Operational default, not vendor-specified:
# GitHub's rate-limit guidance above bounds the per-retry wait, not how many
# retries to attempt before giving up on one gh call.
_PR_COST_RATE_LIMIT_MAX_ELAPSED_S = 15 * 60  # Operational default, not
# vendor-specified: a per-call ceiling generous enough to ride out one
# secondary-rate-limit window without letting a single gh call stall the run.
_PR_COST_GH_PR_LIST_LIMIT = 1000  # gh pr list's own default (30) silently
# truncates any larger population with no error. This repo's own population
# is a few hundred merged PRs; 1000 is a generous fixed ceiling, not a
# per-run population count -- --limit is a
# pagination bound, not a network timeout, so no vendor citation applies here
# the way it does to the backoff constants above.

_GIT_REMOTE_ORIGIN_TIMEOUT_S = 10  # Matches this file's other local git
# calls (_ledger_path_is_git_tracked, _repo_scoped_project_slugs): no
# network/credential work, so this only bounds a wedged invocation.
# Anchored at the start (after an optional scheme/git@ prefix) so the captured
# host is the URL's actual host, never merely a substring appearing later in
# a malicious or misconfigured remote (e.g. https://attacker.example/github.com/x/y) --
# whatever hostname it turns out to be, github.com or a GHE host alike.
_GIT_REMOTE_HOST_OWNER_REPO_RE = re.compile(
    r"^(?:https?://|git://|ssh://(?:git@)?|git@)?(?P<host>[A-Za-z0-9.-]+)[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
)
# The host character class above has no port syntax, so a GHE remote on a
# non-standard port (host:8443, ssh://git@host:2222/...) fails to parse and
# the run aborts rather than misrouting.


# Best-effort classification of a failed gh call's stderr text -- gh has no
# structured error-kind field on stderr, so this is pattern matching against
# gh's own documented error phrasing, not a guarantee.
_GH_AUTH_ERROR_RE = re.compile(r"not logged into|gh auth login|authentication failed|http\s?401", re.IGNORECASE)
# Matches gh's own stderr for an ambient GH_HOST that doesn't match any
# configured git remote (verified against gh 2.97.0: "none of the git
# remotes configured for this repository correspond to the GH_HOST
# environment variable") -- a local shell-config mismatch, not a transient
# failure, so it must not consume the retry budget the way a genuine
# network error does.
_GH_HOST_MISMATCH_ERROR_RE = re.compile(r"GH_HOST environment variable", re.IGNORECASE)
_GH_RATE_LIMIT_ERROR_RE = re.compile(r"rate limit|http\s?429|http\s?403", re.IGNORECASE)
_GH_RETRY_AFTER_RE = re.compile(r"retry.{0,3}after[:\s]+(\d+)", re.IGNORECASE)


def _git_remote_origin_host_and_owner_repo(subcommand: str = "pr-cost", failure_hint: str = "") -> tuple[str, str]:
    """Case-folded (host, owner/name) parsed from this invocation's own
    `git remote get-url origin` -- the corpus-root side of _resolve_pinned_gh_repo's
    identity comparison, run from cwd (this subcommand's own worktree) rather
    than against the ~/.claude/projects/ transcript scan root, which is never
    a git repository itself. Accepts any host (github.com, a GitHub
    Enterprise host, ...); whether gh actually holds credentials for that
    host is left to the caller and to gh itself, not decided by this parse.
    `subcommand` prefixes the failure messages. A non-empty `failure_hint`
    is appended to them as the caller's escape hatch.
    """
    hint_suffix = f" -- {failure_hint}" if failure_hint else ""
    try:
        proc = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=_GIT_REMOTE_ORIGIN_TIMEOUT_S, check=True,
            encoding="utf-8", errors="replace",
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        print(
            f"{subcommand}: could not resolve this repo's own remote (git remote get-url origin failed){hint_suffix}",
            file=sys.stderr,
        )
        sys.exit(1)
    m = _GIT_REMOTE_HOST_OWNER_REPO_RE.search(proc.stdout.strip())
    if not m:
        print(
            f"{subcommand}: this repo's origin remote is not a recognizable host/owner/repo URL{hint_suffix}",
            file=sys.stderr,
        )
        sys.exit(1)
    return m.group("host").lower(), f"{m.group('owner')}/{m.group('repo')}".lower()


_GH_ERROR_KIND_AUTH = "auth"
_GH_ERROR_KIND_HOST_MISMATCH = "host_mismatch"
_GH_ERROR_KIND_RATE_LIMIT = "rate_limit"
_GH_ERROR_KIND_NETWORK = "network"


def _classify_gh_error(stderr: str) -> str:
    """Best-effort classification of a failed gh call's stderr text into
    one of the _GH_ERROR_KIND_* constants."""
    if _GH_AUTH_ERROR_RE.search(stderr):
        return _GH_ERROR_KIND_AUTH
    if _GH_HOST_MISMATCH_ERROR_RE.search(stderr):
        return _GH_ERROR_KIND_HOST_MISMATCH
    if _GH_RATE_LIMIT_ERROR_RE.search(stderr):
        return _GH_ERROR_KIND_RATE_LIMIT
    return _GH_ERROR_KIND_NETWORK


def _parse_gh_retry_after_seconds(stderr: str) -> float | None:
    """Seconds to wait before retrying, parsed from a "retry after N" /
    "retry-after: N" phrase in gh's own stderr text when present, else None
    (caller falls back to the exponential backoff base)."""
    m = _GH_RETRY_AFTER_RE.search(stderr)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _gh_call_with_backoff(argv: Sequence[str], *, label: str) -> tuple[subprocess.CompletedProcess | None, str]:
    """Run one gh call, retrying a rate-limit- or network-shaped failure with
    exponential backoff (starting at _PR_COST_RATE_LIMIT_MIN_BACKOFF_S,
    doubling each attempt, honoring a parsed "retry after" hint from gh's own
    stderr when present) up to _PR_COST_RATE_LIMIT_MAX_ATTEMPTS attempts or
    _PR_COST_RATE_LIMIT_MAX_ELAPSED_S total elapsed -- a budget local to this
    one call (attempt/elapsed/backoff are all function-local state), not
    shared across the run: a --record sweep over many PRs can spend up to
    that budget on each one in the worst case. An auth-shaped or
    GH_HOST-mismatch-shaped failure is never retried: gh auth status already
    ran as a preflight, and a local shell-config mismatch doesn't self-resolve
    by waiting either way.

    Returns (proc, "") on success. On exhaustion, returns (None, status)
    with status one of _GH_CALL_DEGRADED_AUTH, _GH_CALL_DEGRADED_HOST_MISMATCH,
    pr_cost_ledger._PR_COST_STATUS_DEGRADED_RATE_LIMIT, pr_cost_ledger._PR_COST_STATUS_DEGRADED_NETWORK --
    callers with no row yet to degrade (repo-identity resolution, discovery)
    abort the whole run on any non-empty status; per-PR enrichment instead
    marks that row's own status column (folding _GH_CALL_DEGRADED_AUTH and
    _GH_CALL_DEGRADED_HOST_MISMATCH into pr_cost_ledger._PR_COST_STATUS_DEGRADED_NETWORK
    there -- see _PR_COST_STATUS_VALUES).
    """
    attempt = 0
    elapsed = 0.0
    backoff = _PR_COST_RATE_LIMIT_MIN_BACKOFF_S
    while True:
        stderr = ""
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=_GH_CALL_TIMEOUT_S,
                encoding="utf-8", errors="replace",
            )
        except (subprocess.TimeoutExpired, OSError):
            kind = _GH_ERROR_KIND_NETWORK
        else:
            if proc.returncode == 0:
                return proc, ""
            stderr = proc.stderr or ""
            kind = _classify_gh_error(stderr)

        attempt += 1
        if kind == _GH_ERROR_KIND_AUTH:
            print(f"pr-cost: {label} failed ({kind}), not retrying (auth failures don't self-resolve)", file=sys.stderr)
            return None, _GH_CALL_DEGRADED_AUTH
        if kind == _GH_ERROR_KIND_HOST_MISMATCH:
            # Never echoes gh's own stderr (same discipline as every other
            # print site here), but this specific failure has one fix an
            # operator can actually act on, so name it instead of falling
            # through to the generic network-failure message below.
            print(
                f"pr-cost: {label} failed ({kind}), not retrying -- gh's ambient GH_HOST"
                " environment variable does not match this repo's own git remote host;"
                " unset GH_HOST or point it at the correct host",
                file=sys.stderr,
            )
            return None, _GH_CALL_DEGRADED_HOST_MISMATCH
        if attempt >= _PR_COST_RATE_LIMIT_MAX_ATTEMPTS or elapsed >= _PR_COST_RATE_LIMIT_MAX_ELAPSED_S:
            print(f"pr-cost: {label} failed ({kind}), giving up after {attempt} attempt(s)", file=sys.stderr)
            return None, (
                pr_cost_ledger._PR_COST_STATUS_DEGRADED_RATE_LIMIT if kind == _GH_ERROR_KIND_RATE_LIMIT
                else pr_cost_ledger._PR_COST_STATUS_DEGRADED_NETWORK
            )
        # Capped to the remaining elapsed budget: an unbounded or malformed
        # "retry after" hint from gh's own stderr must not let one sleep
        # jump past _PR_COST_RATE_LIMIT_MAX_ELAPSED_S in a single call.
        sleep_for = min(_parse_gh_retry_after_seconds(stderr) or backoff, _PR_COST_RATE_LIMIT_MAX_ELAPSED_S - elapsed)
        print(f"pr-cost: {label} failed ({kind}), retrying in {sleep_for:g}s (attempt {attempt})...", file=sys.stderr)
        time.sleep(sleep_for)
        elapsed += sleep_for
        backoff *= 2


def _pr_cost_abort_on_gh_failure(label: str, degraded: str) -> None:
    """Print a run-abort message and exit(1) for a gh call that has no row
    yet to degrade into (repo-identity resolution, or discovery) -- only
    these two calls abort the whole run; every later per-PR `gh pr view`
    failure degrades that row's status instead (see _pr_cost_report's main
    loop). Never echoes gh's own raw stderr text (the underlying diagnostic
    gh emits, which can itself echo the queried repo verbatim) -- only this
    module's own `degraded` classification reaches stdout/stderr.
    """
    print(f"pr-cost: {label} failed ({degraded}) before any row could be captured", file=sys.stderr)
    sys.exit(1)


def _gh_auth_preflight_ok(hostname: str) -> bool:
    """A single, non-retried `gh auth status --hostname` check, run before
    anything else in this subcommand -- an auth failure caught here is
    cheaper than one surfacing mid-run after a local corpus scan and gh
    discovery call. Scoped to one host because a bare `gh auth status`
    evaluates every host it has ever held credentials for and fails
    aggregate-wide on any one of them, including hosts irrelevant to this
    run (e.g. a GHE-only token still triggers a github.com check)."""
    try:
        proc = subprocess.run(
            ["gh", "auth", "status", "--hostname", hostname], capture_output=True, text=True,
            timeout=_GH_CALL_TIMEOUT_S, encoding="utf-8", errors="replace",
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


def _resolve_pinned_gh_repo(corpus_host: str, corpus_repo: str, ordinal: int) -> tuple[str, dict]:
    """Resolve gh's effective repo identity once, refuse (exit 2) if its
    host or owner/name (case-folded) disagrees with corpus_host/corpus_repo
    (this repo's own git remote identity, resolved by the caller), and
    return the confirmed owner/name to pin on every subsequent gh call this
    run makes -- gh's ambient target repo (a stale GH_REPO, `gh repo
    set-default`, or an ambient cwd mismatch) can otherwise silently
    diverge from the repo this invocation's own corpus and git remote
    actually belong to, including a same-named repo on a different host.
    Also returns a fresh repo-kind redact map, used to scrub this same repo
    value at every later print site this run needs. `ordinal` is the
    redact label to use for this call's own mismatch-refusal message --
    this resolution happens once per run, before any single account is
    "the" account under --all-accounts, so the caller supplies it rather
    than this function hardcoding one.
    """
    # The mismatch check below folds a gh-side parse failure into gh_host=""
    # and relies on that never coincidentally equaling corpus_host -- true
    # today only because the sole caller resolves corpus_host via
    # _git_remote_origin_host_and_owner_repo(), which itself never returns
    # an empty string. Assert it here so a future caller violating that
    # invariant fails loud instead of silently disabling the mismatch check.
    if not corpus_host or not corpus_repo:
        raise ValueError("_resolve_pinned_gh_repo requires a non-empty corpus_host and corpus_repo")
    proc, degraded = _gh_call_with_backoff(
        ["gh", "repo", "view", "--json", "nameWithOwner,url"], label="repo view"
    )
    if degraded:
        _pr_cost_abort_on_gh_failure("gh repo view", degraded)
    try:
        payload = json.loads(proc.stdout or "{}")
        gh_repo = str(payload["nameWithOwner"]).lower()
        gh_url = str(payload["url"])
    except (json.JSONDecodeError, KeyError, TypeError):
        print("pr-cost: gh repo view returned unparseable JSON -- no row was captured", file=sys.stderr)
        sys.exit(1)
    # url is the same https://host/owner/repo shape _GIT_REMOTE_HOST_OWNER_REPO_RE
    # already parses for the local git remote, so reuse it here instead of a
    # second host-parsing implementation.
    url_match = _GIT_REMOTE_HOST_OWNER_REPO_RE.search(gh_url)
    gh_host = url_match.group("host").lower() if url_match else ""

    repo_map: dict[tuple[int, str], str] = {}
    if gh_host != corpus_host or gh_repo != corpus_repo:
        print(
            "pr-cost: gh's effective target repo does not match this repo's own git remote identity"
            f" ({redaction._assign_root_scoped_redact_label('repo', ordinal, f'{gh_host}/{gh_repo}', repo_map)} vs."
            f" {redaction._assign_root_scoped_redact_label('repo', ordinal, f'{corpus_host}/{corpus_repo}', repo_map)}) --"
            " check GH_REPO, `gh repo set-default`, or an ambient cwd mismatch",
            file=sys.stderr,
        )
        sys.exit(2)
    return gh_repo, repo_map


def _gh_host_qualified_repo(corpus_host: str, pinned_repo: str) -> str:
    """`HOST/OWNER/REPO` form for a gh `--repo` argument -- gh's bare
    `OWNER/REPO` form resolves against whichever host the ambient `GH_HOST`
    environment variable names (api.github.com when unset), regardless of
    the invoking directory's own git remote, so every gh call this
    subcommand makes must host-qualify `--repo` to actually reach the
    intended host instead of silently querying the wrong one under the
    same owner/repo.
    """
    return f"{corpus_host}/{pinned_repo}"


def _gh_discover_merged_prs(corpus_host: str, pinned_repo: str) -> list[dict]:
    """Bulk-discover every merged PR for the pinned repo in one call, with
    an explicit --limit -- never gh's own 30-item default, which would
    silently truncate a larger population with no error. Auth/config-shaped
    failures abort the whole run immediately (no retry); rate-limit/network
    failures retry under the shared backoff budget before aborting the same
    way -- discovery has no per-row granularity to degrade into. `--repo` is
    host-qualified (see _gh_host_qualified_repo) so a GHE-pinned repo is
    queried on its own host rather than on api.github.com.
    """
    argv = [
        "gh", "pr", "list", "--repo", _gh_host_qualified_repo(corpus_host, pinned_repo), "--state", "merged",
        "--limit", str(_PR_COST_GH_PR_LIST_LIMIT),
        "--json", "number,headRefName,additions,deletions,changedFiles,mergedAt",
    ]
    proc, degraded = _gh_call_with_backoff(argv, label="pr list")
    if degraded:
        _pr_cost_abort_on_gh_failure("gh pr list", degraded)
    try:
        return json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        print("pr-cost: gh pr list returned unparseable JSON -- no row was captured", file=sys.stderr)
        sys.exit(1)


def _gh_discover_closed_unmerged_pr_branches(corpus_host: str, pinned_repo: str) -> set[str]:
    """Bulk-discover the headRefName of every closed-but-unmerged PR for the
    pinned repo -- same call shape as _gh_discover_merged_prs, --state
    closed instead of --state merged. gh's own PR state model keeps "merged"
    and "closed" disjoint (a merged PR's state is MERGED, never CLOSED), so
    this tells workstream-cost's abandoned-branch check "had a PR that was
    closed without merging" apart from "never had a PR at all" -- a branch
    absent from both this set and _gh_discover_merged_prs' own result.
    """
    argv = [
        "gh", "pr", "list", "--repo", _gh_host_qualified_repo(corpus_host, pinned_repo), "--state", "closed",
        "--limit", str(_PR_COST_GH_PR_LIST_LIMIT),
        "--json", "headRefName",
    ]
    proc, degraded = _gh_call_with_backoff(argv, label="pr list (closed)")
    if degraded:
        _pr_cost_abort_on_gh_failure("gh pr list (closed)", degraded)
    try:
        payload = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        print("pr-cost: gh pr list (closed) returned unparseable JSON", file=sys.stderr)
        sys.exit(1)
    return {pr["headRefName"] for pr in payload if pr.get("headRefName")}
