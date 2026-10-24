"""gh and git-remote access shared by pr-link, pr-cost, workstream-cost, and period-cost: origin host/owner/repo parsing,
gh stderr classification, rate-limit backoff, auth preflight, effective-repo pinning, and merged/closed PR discovery.

Imports pr_cost_ledger and redaction by module (attribute access, not by name) -- see scope.py's own top-of-file comment for
why."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence

from transcript_analysis import pr_cost_ledger, redaction

# The gh-call-level outcomes _gh_call_with_backoff itself returns on an
# auth-shaped or local-misconfiguration-shaped failure -- never ledger
# status values; every caller folds them into pr_cost_ledger._PR_COST_STATUS_DEGRADED_NETWORK
# before they reach a row (see pr_cost_ledger.py's status enum).
_GH_CALL_DEGRADED_AUTH = "degraded_auth"
_GH_CALL_DEGRADED_HOST_MISMATCH = "degraded_host_mismatch"
# _gh_pr_list's status when gh succeeded but its stdout was not JSON; distinct from every degraded status above
# and from pr_cost_ledger's status values, so a caller can tell "gh failed" from "gh answered unparseably".
_GH_PR_LIST_UNPARSEABLE = "unparseable_json"
# gh reads these as auth tokens; a child that must not send an ambient gh token gets an environment without them.
_GH_TOKEN_ENV_VARS = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")


def _environ_without_gh_tokens() -> dict[str, str]:
    """A copy of os.environ without any of _GH_TOKEN_ENV_VARS."""
    return {name: value for name, value in os.environ.items() if name not in _GH_TOKEN_ENV_VARS}


_GH_CALL_TIMEOUT_S = 30.0  # Operational default: gh publishes no single
# per-call timeout recommendation, so this is a considered guess generous
# enough for one REST round trip, not a network SLA citation. It does not cover
# the bulk `gh pr list`, which has _GH_PR_LIST_TIMEOUT_S.
_GH_PR_LIST_TIMEOUT_S = 90.0  # Operational default, not vendor-specified: one
# manual run of `gh pr list --state merged --limit 1000` took about 34 s (the
# item count it returned was not recorded), and 90 s is roughly 2.6 times that
# for a slower network. The `--state all` call period-cost makes was not
# measured.
_PR_COST_RATE_LIMIT_MIN_BACKOFF_S = 60.0  # GitHub REST API docs, "Rate
# limits for the REST API" -- secondary-rate-limit guidance: wait at least
# one minute between retries when no Retry-After header is present.
_PR_COST_RATE_LIMIT_MAX_ATTEMPTS = 5  # Operational default, not vendor-specified:
# GitHub's rate-limit guidance above bounds the per-retry wait, not how many
# retries to attempt before giving up on one gh call.
_PR_COST_RATE_LIMIT_MAX_ELAPSED_S = 15 * 60  # Operational default, not
# vendor-specified: a per-call ceiling on accumulated backoff sleep time,
# generous enough to ride out one secondary-rate-limit window. It does not bound
# the time each gh attempt itself runs.
_PR_COST_GH_PR_LIST_LIMIT = 1000  # gh pr list's own default (30) silently
# truncates any larger population with no error. This repo's own population
# is a few hundred merged PRs; 1000 is a generous fixed ceiling, not a
# per-run population count -- --limit is a
# pagination bound, not a network timeout, so no vendor citation applies here
# the way it does to the backoff constants above.

_GIT_REMOTE_ORIGIN_TIMEOUT_S = 10  # Matches this file's other local git
# calls (_ledger_path_is_git_tracked, _repo_scoped_project_slugs): no
# network/credential work, so this only bounds a wedged invocation. It also
# bounds _origin_identity_at, which runs against arbitrary recorded directories
# (any of which may sit on a slow mount), and the value was not measured against one.
# Anchored at the start (after an optional scheme/git@ prefix) so the captured
# host is the URL's actual host, never merely a substring appearing later in
# a malicious or misconfigured remote (e.g. https://attacker.example/github.com/x/y) --
# whatever hostname it turns out to be, github.com or a GHE host alike.
_GIT_REMOTE_HOST_OWNER_REPO_RE = re.compile(
    r"^(?P<scheme>(?:https?://|git://|ssh://(?:git@)?|git@)?)(?P<host>[A-Za-z0-9.-]+)(?P<sep>[:/])"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
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


def _origin_url_match(url: str) -> re.Match[str] | None:
    return _GIT_REMOTE_HOST_OWNER_REPO_RE.search(url.strip())


def _origin_identity_from_url(url: str) -> tuple[str, str] | None:
    """Case-folded (host, owner/name) parsed from a git remote URL, or None when the URL is not a recognizable
    host/owner/repo shape. Lenient by design: it accepts every shape `_GIT_REMOTE_HOST_OWNER_REPO_RE` matches,
    including shapes `_vetted_origin_identity_from_url` rejects."""
    m = _origin_url_match(url)
    if m is None:
        return None
    return m.group("host").lower(), f"{m.group('owner')}/{m.group('repo')}".lower()


# A plain owner/repo segment is one or more of [A-Za-z0-9._-] holding at least one ASCII letter or digit.
# Two linear scans instead of one pattern with a nested star, which backtracks quadratically on a failing input.
_PLAIN_SEGMENT_CHARACTERS_RE = re.compile(r"[A-Za-z0-9._-]+")
_ASCII_LETTER_OR_DIGIT_RE = re.compile(r"[A-Za-z0-9]")
# Labels of ASCII letters, digits, and hyphens, each starting and ending with a letter or digit (RFC 1123 section 2.1).
_PLAIN_HOST_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*")


def _is_plain_owner_repo(owner_repo: str) -> bool:
    """True when `owner_repo` fully matches the owner/repo grammar of _lib_parse_pr_identity in
    claude/.claude/hooks/_lib.sh, which this mirrors. Costs time linear in the length of `owner_repo`."""
    segments = owner_repo.split("/")
    return len(segments) == 2 and all(
        _PLAIN_SEGMENT_CHARACTERS_RE.fullmatch(segment) is not None and _ASCII_LETTER_OR_DIGIT_RE.search(segment) is not None
        for segment in segments
    )


def _vetted_origin_identity_from_url(url: str) -> tuple[str, str] | None:
    """Like _origin_identity_from_url, but None unless the URL has a plain form, host, and owner/name.

    A recorded directory's contents are untrusted, so a credentialed URL, relative path, port, or transport helper
    never becomes a repository identity."""
    m = _origin_url_match(url)
    if m is None:
        return None
    expected_separator = "/" if "://" in m.group("scheme") else ":"
    if m.group("sep") != expected_separator:
        return None
    if _PLAIN_HOST_RE.fullmatch(m.group("host")) is None:
        return None
    owner_repo = f"{m.group('owner')}/{m.group('repo')}"
    if not _is_plain_owner_repo(owner_repo):
        return None
    return m.group("host").lower(), owner_repo.lower()


# The list `git rev-parse --local-env-vars` prints. Left in the environment they would point `git -C <directory>`
# at the exporter's repository instead of the one under <directory>.
_GIT_REPOSITORY_LOCAL_ENV_VARS = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
    "GIT_OBJECT_DIRECTORY", "GIT_DIR", "GIT_WORK_TREE", "GIT_IMPLICIT_WORK_TREE", "GIT_GRAFT_FILE",
    "GIT_INDEX_FILE", "GIT_NO_REPLACE_OBJECTS", "GIT_REPLACE_REF_BASE", "GIT_PREFIX", "GIT_SHALLOW_FILE",
    "GIT_COMMON_DIR",
)


def _origin_identity_at(directory: object) -> tuple[str, str] | None:
    """Case-folded (host, owner/name) of the `origin` remote of the repository containing `directory`, or None
    when `directory` is not an absolute path, is in no repository, the repository has no origin, git fails or times
    out, or the URL fails _vetted_origin_identity_from_url. Prints nothing: the caller decides what an unresolvable
    directory means.

    The returned host is not checked for a gh login. The caller's stored-login check is the control that keeps an
    ambient token from reaching a host that only an `origin` named. The child's environment carries neither git's
    repository-local variables nor gh's token variables. The directory's contents are untrusted while the
    environment is the engineer's own, so that scrub is a correctness guard, not a boundary.
    """
    try:
        directory_path = os.fspath(directory)
    except TypeError:
        return None
    if not isinstance(directory_path, str) or not os.path.isabs(directory_path):
        return None
    env = {
        name: value for name, value in os.environ.items()
        if name not in _GIT_REPOSITORY_LOCAL_ENV_VARS and name not in _GH_TOKEN_ENV_VARS
    }
    home = os.environ.get("HOME", "")
    if home and ":" not in home and os.path.isabs(home):
        # The ceiling stops a home-directory repository from claiming folders under it.
        # Git splits the list on colons and skips relative entries, so a colon-bearing or relative HOME gets no ceiling.
        # HOME goes first so it sits before any empty entry, past which git skips symlink resolution.
        inherited_ceilings = os.environ.get("GIT_CEILING_DIRECTORIES", "")
        env["GIT_CEILING_DIRECTORIES"] = f"{home}:{inherited_ceilings}" if inherited_ceilings else home
    try:
        proc = subprocess.run(
            # --no-pager keeps a repository's pager setting unrun whatever stdout is.
            # The -c keeps a committed bare-repository layout from being read as a repository on git 2.38 or later.
            ["git", "--no-pager", "-c", "safe.bareRepository=explicit", "-C", directory_path, "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=_GIT_REMOTE_ORIGIN_TIMEOUT_S,
            encoding="utf-8", errors="replace", env=env,
        )
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None
    if proc.returncode != 0:
        return None
    return _vetted_origin_identity_from_url(proc.stdout)


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
    identity = _origin_identity_from_url(proc.stdout)
    if identity is None:
        print(
            f"{subcommand}: this repo's origin remote is not a recognizable host/owner/repo URL{hint_suffix}",
            file=sys.stderr,
        )
        sys.exit(1)
    return identity


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


def _gh_call_with_backoff(
    argv: Sequence[str], *, label: str, command_name: str = "pr-cost", max_attempts: int | None = None,
    env: Mapping[str, str] | None = None, timeout_s: float | None = None,
) -> tuple[subprocess.CompletedProcess | None, str]:
    """Run one gh call, retrying a rate-limit- or network-shaped failure with
    exponential backoff (starting at _PR_COST_RATE_LIMIT_MIN_BACKOFF_S,
    doubling each attempt, honoring a parsed "retry after" hint from gh's own
    stderr when present) up to `max_attempts` attempts (None reads
    _PR_COST_RATE_LIMIT_MAX_ATTEMPTS at call time) or
    _PR_COST_RATE_LIMIT_MAX_ELAPSED_S of accumulated backoff sleep time (the
    time each gh attempt runs is not counted) -- a budget local to this one
    call (attempt/elapsed/backoff are all function-local state), not shared
    across the run: a --record sweep over many PRs can spend up to that budget
    on each one in the worst case. An auth-shaped or
    GH_HOST-mismatch-shaped failure is never retried: gh auth status already
    ran as a preflight, and a local shell-config mismatch doesn't self-resolve
    by waiting either way. A `max_attempts` below 1 raises ValueError.
    `command_name` prefixes every message this function prints. `env` is the child's environment; None inherits
    this process's. `timeout_s` bounds each attempt's gh process (None reads
    _GH_CALL_TIMEOUT_S at call time); a timeout counts as a network failure and follows the same attempt limit.

    Returns (proc, "") on success. On exhaustion, returns (None, status)
    with status one of _GH_CALL_DEGRADED_AUTH, _GH_CALL_DEGRADED_HOST_MISMATCH,
    pr_cost_ledger._PR_COST_STATUS_DEGRADED_RATE_LIMIT, pr_cost_ledger._PR_COST_STATUS_DEGRADED_NETWORK --
    callers with no row yet to degrade (repo-identity resolution, discovery)
    abort the whole run on any non-empty status; per-PR enrichment instead
    marks that row's own status column (folding _GH_CALL_DEGRADED_AUTH and
    _GH_CALL_DEGRADED_HOST_MISMATCH into pr_cost_ledger._PR_COST_STATUS_DEGRADED_NETWORK
    there -- see _PR_COST_STATUS_VALUES).
    """
    if max_attempts is not None and max_attempts < 1:
        raise ValueError(f"max_attempts must be at least 1, got {max_attempts}")
    attempts_allowed = _PR_COST_RATE_LIMIT_MAX_ATTEMPTS if max_attempts is None else max_attempts
    attempt_timeout_s = _GH_CALL_TIMEOUT_S if timeout_s is None else timeout_s
    attempt = 0
    elapsed = 0.0
    backoff = _PR_COST_RATE_LIMIT_MIN_BACKOFF_S
    while True:
        stderr = ""
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=attempt_timeout_s,
                encoding="utf-8", errors="replace", env=env,
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
            print(f"{command_name}: {label} failed ({kind}), not retrying (auth failures don't self-resolve)", file=sys.stderr)
            return None, _GH_CALL_DEGRADED_AUTH
        if kind == _GH_ERROR_KIND_HOST_MISMATCH:
            # Never echoes gh's own stderr (same discipline as every other
            # print site here), but this specific failure has one fix an
            # operator can actually act on, so name it instead of falling
            # through to the generic network-failure message below.
            print(
                f"{command_name}: {label} failed ({kind}), not retrying -- gh's ambient GH_HOST"
                " environment variable does not match this repo's own git remote host;"
                " unset GH_HOST or point it at the correct host",
                file=sys.stderr,
            )
            return None, _GH_CALL_DEGRADED_HOST_MISMATCH
        if attempt >= attempts_allowed or elapsed >= _PR_COST_RATE_LIMIT_MAX_ELAPSED_S:
            print(f"{command_name}: {label} failed ({kind}), giving up after {attempt} attempt(s)", file=sys.stderr)
            return None, (
                pr_cost_ledger._PR_COST_STATUS_DEGRADED_RATE_LIMIT if kind == _GH_ERROR_KIND_RATE_LIMIT
                else pr_cost_ledger._PR_COST_STATUS_DEGRADED_NETWORK
            )
        # Capped to the remaining elapsed budget: an unbounded or malformed
        # "retry after" hint from gh's own stderr must not let one sleep
        # jump past _PR_COST_RATE_LIMIT_MAX_ELAPSED_S in a single call.
        sleep_for = min(_parse_gh_retry_after_seconds(stderr) or backoff, _PR_COST_RATE_LIMIT_MAX_ELAPSED_S - elapsed)
        print(f"{command_name}: {label} failed ({kind}), retrying in {sleep_for:g}s (attempt {attempt})...", file=sys.stderr)
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


def _gh_pr_list(
    corpus_host: str, repo: str, *, state: str, json_fields: str, label: str,
    command_name: str = "pr-cost", max_attempts: int | None = None, env: Mapping[str, str] | None = None,
) -> tuple[object | None, str]:
    """One bulk `gh pr list` for `repo` in `state` with an explicit --limit, host-qualified (see
    _gh_host_qualified_repo). Returns (payload, "") on success, (None, <degraded status>) when the shared backoff
    gave up, or (None, _GH_PR_LIST_UNPARSEABLE) when gh's stdout is not JSON. Applies no shape check to the payload
    and prints nothing itself; the backoff prints its own messages under `command_name`. `env` is forwarded to the call.
    Each attempt is bounded by _GH_PR_LIST_TIMEOUT_S rather than the shorter default."""
    argv = [
        "gh", "pr", "list", "--repo", _gh_host_qualified_repo(corpus_host, repo), "--state", state,
        "--limit", str(_PR_COST_GH_PR_LIST_LIMIT), "--json", json_fields,
    ]
    proc, degraded = _gh_call_with_backoff(
        argv, label=label, command_name=command_name, max_attempts=max_attempts, env=env, timeout_s=_GH_PR_LIST_TIMEOUT_S,
    )
    if degraded:
        return None, degraded
    try:
        return json.loads(proc.stdout or "[]"), ""
    except json.JSONDecodeError:
        return None, _GH_PR_LIST_UNPARSEABLE


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
    payload, degraded = _gh_pr_list(
        corpus_host, pinned_repo, state="merged", json_fields="number,headRefName,additions,deletions,changedFiles,mergedAt",
        label="pr list",
    )
    if degraded == _GH_PR_LIST_UNPARSEABLE:
        print("pr-cost: gh pr list returned unparseable JSON -- no row was captured", file=sys.stderr)
        sys.exit(1)
    if degraded:
        _pr_cost_abort_on_gh_failure("gh pr list", degraded)
    return payload


def _gh_discover_closed_unmerged_pr_branches(corpus_host: str, pinned_repo: str) -> set[str]:
    """Bulk-discover the headRefName of every closed-but-unmerged PR for the
    pinned repo -- same call shape as _gh_discover_merged_prs, --state
    closed instead of --state merged. gh's own PR state model keeps "merged"
    and "closed" disjoint (a merged PR's state is MERGED, never CLOSED), so
    this tells workstream-cost's abandoned-branch check "had a PR that was
    closed without merging" apart from "never had a PR at all" -- a branch
    absent from both this set and _gh_discover_merged_prs' own result.
    """
    payload, degraded = _gh_pr_list(
        corpus_host, pinned_repo, state="closed", json_fields="headRefName", label="pr list (closed)",
    )
    if degraded == _GH_PR_LIST_UNPARSEABLE:
        print("pr-cost: gh pr list (closed) returned unparseable JSON", file=sys.stderr)
        sys.exit(1)
    if degraded:
        _pr_cost_abort_on_gh_failure("gh pr list (closed)", degraded)
    return {pr["headRefName"] for pr in payload if pr.get("headRefName")}
