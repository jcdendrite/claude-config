"""PR-comment miner: finds evals/review_bench candidates from the repo
owner's own inline review comments on merged pull requests that a
`respond-pr` reply marked fixed.

Reads GitHub through `gh` only (no token handling of its own), for this
checkout's `origin` repository on github.com when the provider reports it
public. The head commit comes from blaming the commented line and the fix
commit from the thread's FIXED reply, so the miner shares only PR-head
fetching and the blame flags with `mine_review_rounds` and `mine_szz`, not
`mine_review_rounds.resolve_defect_commits`' path-and-time rule.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import chain
from pathlib import Path
from typing import NamedTuple, NoReturn
from urllib.parse import urlsplit

from review_bench import mine_review_rounds, mine_szz
from review_bench.defects import (
    _SHA_RE,
    STORED_DESCRIPTION_ALLOWED,
    Candidate,
    assert_unique_ids,
    escape_for_terminal,
    first_disallowed_character,
    guess_lens,
    has_disallowed_control_character,
    is_invisible_character,
    is_markdown_path,
)

# `gh api` resolves against `gh`'s default host, so this miner pins the host on
# every call and refuses any `origin` that does not name it.
_GITHUB_HOST = "github.com"

# `respond-pr` posts its replies through the owner's own token and opens each
# with this marker, so authorship alone does not separate them from the owner's
# comments. It restates RESPOND_PR_OWNERSHIP_MARKER in
# claude/.claude/scripts/_respond-pr-lib.sh, which Python cannot import.
RESPOND_PR_OWNERSHIP_MARKER = "**[Claude Code]**"

# The four classes `classify_reply` assigns a marked reply. The last three are
# also the skip reasons a thread without a FIXED reply is counted under.
REPLY_FIXED = "FIXED"
REPLY_FIXED_WITHOUT_SHA = "fixed-without-sha"
REPLY_OUT_OF_SCOPE_OR_DEFERRED = "out-of-scope-or-deferred"
REPLY_NOT_FIXED = "not-fixed"

# `respond-pr` writes these field tokens in its replies
# (claude-skills/skills/respond-pr/SKILL.md, step 5). A FIXED reply holds a
# `disposition: fixed-...` token and a `commit_sha:` value of 7 to 40 hex
# characters.
_FIXED_DISPOSITION_RE = re.compile(r"disposition:[ \t]*`?fixed-")
_COMMIT_SHA_VALUE_RE = re.compile(r"commit_sha:[ \t]*`?([0-9a-fA-F]{7,40})(?![0-9A-Za-z])")
_DEFERRAL_FIELD_TOKENS = ("where-tracked", "deferral-reason", "follow-up-ticket")

# `pull_request_url` ends `/pulls/<number>`.
_PULL_REQUEST_URL_NUMBER_RE = re.compile(r"/pulls/(\d+)$")

# One path segment of an `owner/repo` slug, the grammar `_respond-pr-lib.sh`'s
# `respond_pr_valid_repo_slug` enforces.
_REPOSITORY_SEGMENT_RE = re.compile(r"[A-Za-z0-9._-]+")
# `[user@]host:path`, the scp-like remote shape.
_SCP_LIKE_REMOTE_RE = re.compile(r"(?:[^@/:]+@)?([^@/:]+):(?!//)(.+)")
_LOGIN_RE = re.compile(r"[A-Za-z0-9_-]+")

# A `git blame --porcelain` group header: "<sha> <source-line> <final-line> [<group-size>]".
_BLAME_HEADER_RE = re.compile(r"([0-9a-f]{40}) (\d+) (\d+)(?: \d+)?")

# A full paginated listing is many REST round trips, so each listing gets its
# own bound instead of mine_review_rounds._GH_CALL_TIMEOUT_S, which covers one.
# GitHub's REST documentation states no latency bound and neither magnitude
# is measured, so each is a hang guard that fails the run loudly (exit 2, the
# timeout named in the message). The closed-PR listing returns whole PR
# objects and grows with the repository's PR count, so its guard is the larger.
_GH_COMMENTS_LISTING_TIMEOUT_S = 120.0
_GH_CLOSED_PULLS_LISTING_TIMEOUT_S = 300.0

# GitHub REST pagination ("Using pagination in the REST API"): `per_page` takes
# at most 100, so this is the fewest round trips a listing can need.
_GH_LISTING_PAGE_SIZE = 100

_GH_ERRORS = (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError)

# `git` exits 128 from `die()`. It uses that status for a missing object, a
# missing path, and a line past a file's end, so a call whose negative answer
# is one of those reads 128 as a negative only after checking the object exists.
_GIT_FATAL_STATUS = 128

# A skip reason whose cause is the PR head's fetch, not the comment: the same
# comment can be mined once the head is fetchable. The cause is often transient
# (network, auth, a ref-lock collision in the shared .git). It repeats on every
# rerun for a pull ref the remote lacks and for a head whose fetch exceeds
# `mine_review_rounds._GIT_FETCH_TIMEOUT_S`. `mine` exits 2 and writes nothing
# when any is counted. No option skips one PR or gives its fetch a longer
# timeout, because text-only PRs sit below the size where that matters.
# It is a `resolve_branch_ref` outcome that is also a skip reason.
_REF_STATUS_FETCH_FAILED = "fetch-failed"
# A per-comment skip: a rerun retries the comment, and a comment whose git call
# fails again fails for that comment, e.g. a gitlink path, or a blame that runs
# past `mine_review_rounds._LOCAL_GIT_TIMEOUT_S`.
_GIT_ERROR_REASON = "git-error"

GhRun = Callable[..., subprocess.CompletedProcess]


def _fail(message: str) -> NoReturn:
    print(f"mine-pr-comments: {message}", file=sys.stderr)
    sys.exit(2)


# --- Repository and provider checks -----------------------------------------


def _repository_from_remote_url(url: str) -> str | None:
    """`owner/repo` when `url` names a repository on exactly `github.com`, else
    None. The host comparison is exact: a suffix, userinfo, port, case, or
    trailing-dot variant of the host does not match. A scheme other than
    https and ssh, and a query or fragment, do not match either."""
    if "://" in url:
        try:
            parts = urlsplit(url)
        except ValueError:
            return None
        if parts.scheme not in {"https", "ssh"} or parts.query or parts.fragment:
            return None
        host, path = parts.netloc.rpartition("@")[2], parts.path
    else:
        match = _SCP_LIKE_REMOTE_RE.fullmatch(url)
        if match is None:
            return None
        host, path = match.groups()
    if host != _GITHUB_HOST:
        return None
    segments = path.removeprefix("/").removesuffix("/").removesuffix(".git").split("/")
    if len(segments) != 2 or not all(_REPOSITORY_SEGMENT_RE.fullmatch(segment) for segment in segments):
        return None
    if any(set(segment) == {"."} for segment in segments):
        return None
    return "/".join(segments)


def resolve_origin_repository(repo_dir: Path) -> str:
    """The `owner/repo` of `repo_dir`'s `origin` remote, resolved once. Exits 2
    with a fixed message unless `origin` names a repository on github.com. The
    message holds no part of the remote, so a refusal names no repository."""
    try:
        url = mine_szz._run_git(["remote", "get-url", "origin"], cwd=repo_dir).strip()
    except mine_szz._GIT_CALL_ERRORS:
        url = ""
    repository = _repository_from_remote_url(url)
    if repository is None:
        _fail(
            f"origin remote is missing or does not name a repository on {_GITHUB_HOST}, "
            "or git could not read this checkout"
        )
    return repository


def _run_gh_api(
    repo_dir: Path, endpoint: str, *, what: str, run: GhRun, timeout_s: float,
    flags: Sequence[str] = (), show_detail: bool = True,
) -> str:
    """Stdout of one `gh api` call against `--hostname github.com`. Exits 2 on
    failure: this miner has no other data source, so a failed call cannot
    degrade to a partial run. `what` labels the call in the failure message.
    With `show_detail` the message also carries `gh`'s stderr, or `str(exc)`
    when there is none, and `str(exc)` holds the whole command, `endpoint`
    included. `show_detail` False drops all of it, so the visibility call,
    which runs before the repository is known public, names no repository.
    The detail is escaped, because `gh`'s stderr is provider-sourced text."""
    try:
        result = run(
            ["gh", "api", "--hostname", _GITHUB_HOST, endpoint, *flags],
            cwd=repo_dir, capture_output=True, text=True, timeout=timeout_s, check=True,
        )
    except _GH_ERRORS as exc:
        detail = ""
        if show_detail:
            detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) and exc.stderr else str(exc)
            detail = f": {escape_for_terminal(detail)}"
        _fail(f"gh api {what} failed ({type(exc).__name__}){detail}")
    return result.stdout


def require_public_repo(repo_dir: Path, repository: str, *, run: GhRun = subprocess.run) -> None:
    """Exits 2 unless the provider reports `repository` public. The comment
    text becomes committed content, so any unexpected answer refuses: only the
    JSON boolean `private: false` proceeds, and a present `visibility` other
    than "public" refuses beside it. A refusal names no repository."""
    stdout = _run_gh_api(
        repo_dir, f"repos/{repository}", what="repository visibility", run=run,
        timeout_s=mine_review_rounds._GH_CALL_TIMEOUT_S, show_detail=False,
    )
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        payload = None
    is_public = (
        isinstance(payload, dict)
        and payload.get("private") is False
        and payload.get("visibility", "public") == "public"
    )
    if not is_public:
        _fail("refusing to run, the provider did not report the repository as public")


def discover_author_login(repo_dir: Path, *, run: GhRun = subprocess.run) -> str:
    """The authenticated `gh` user's login, never taken as an argument."""
    stdout = _run_gh_api(
        repo_dir, "user", what="user", run=run, timeout_s=mine_review_rounds._GH_CALL_TIMEOUT_S,
        flags=("--jq", ".login"),
    )
    login = stdout.strip()
    if not _LOGIN_RE.fullmatch(login):
        _fail("gh api user returned no usable login")
    return login


# --- GitHub listings ----------------------------------------------------------


def _decode_paginated_pages(stdout: str, *, non_object_reason: str, stats: Counter) -> list[dict]:
    """The objects from `gh api --paginate`'s output, which `gh api --help`
    describes as one separate JSON array per page. Pages are decoded one after
    another whatever separates them. A page item that is not an object is
    dropped and counted under `non_object_reason`."""
    decoder = json.JSONDecoder()
    items: list[dict] = []
    position = 0
    while True:
        while position < len(stdout) and stdout[position].isspace():
            position += 1
        if position >= len(stdout):
            return items
        try:
            page, position = decoder.raw_decode(stdout, position)
        except json.JSONDecodeError:
            _fail("gh returned a listing page that is not JSON")
        if not isinstance(page, list):
            _fail("gh returned a listing page that is not a JSON array")
        for item in page:
            if isinstance(item, dict):
                items.append(item)
            else:
                stats[non_object_reason] += 1


def _fetch_listing(
    repo_dir: Path, repository: str, endpoint: str, *, what: str, timeout_s: float, non_object_reason: str,
    run: GhRun, stats: Counter,
) -> list[dict]:
    stdout = _run_gh_api(
        repo_dir, f"repos/{repository}/{endpoint}", what=what, run=run, timeout_s=timeout_s, flags=("--paginate",),
    )
    return _decode_paginated_pages(stdout, non_object_reason=non_object_reason, stats=stats)


def fetch_review_comments(
    repo_dir: Path, repository: str, *, stats: Counter, run: GhRun = subprocess.run,
) -> list[dict]:
    """Every inline PR review comment in the repo, replies included."""
    return _fetch_listing(
        repo_dir, repository, f"pulls/comments?per_page={_GH_LISTING_PAGE_SIZE}", what="pulls/comments listing",
        timeout_s=_GH_COMMENTS_LISTING_TIMEOUT_S, non_object_reason="non-object-comment-item", run=run, stats=stats,
    )


def fetch_merged_pr_branches(
    repo_dir: Path, repository: str, *, stats: Counter, run: GhRun = subprocess.run,
) -> dict[int, str]:
    """PR number -> head branch name for every merged PR, from one paginated
    listing rather than a lookup per comment. The merged filter runs here, in
    Python, not in a `--jq` string."""
    branches: dict[int, str] = {}
    for pull_request in _fetch_listing(
        repo_dir, repository, f"pulls?state=closed&per_page={_GH_LISTING_PAGE_SIZE}", what="closed pulls listing",
        timeout_s=_GH_CLOSED_PULLS_LISTING_TIMEOUT_S, non_object_reason="non-object-pull-item", run=run, stats=stats,
    ):
        number = pull_request.get("number")
        head = pull_request.get("head")
        branch = head.get("ref") if isinstance(head, dict) else None
        if (
            _is_plain_int(number) and pull_request.get("merged_at") is not None
            and isinstance(branch, str) and branch
        ):
            branches[number] = branch
    return branches


# --- Comment and reply classification -----------------------------------------


def _is_plain_int(value: object) -> bool:
    """A JSON integer. `bool` is an `int` subclass, and `true` is not an id,
    a line, or a PR number."""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_repo_relative_path(path: str) -> bool:
    """Whether `path` can name a tracked file: no control or invisible
    character, and no empty, `.`, or `..` segment, so `<commit>:<path>` cannot
    read as relative to the working directory or leave the repository."""
    return not has_disallowed_control_character(path, allowed=frozenset()) and not any(
        segment in {"", ".", ".."} for segment in path.split("/")
    )


def _opens_with_marker(body: str) -> bool:
    """Whether `body` opens with the ownership marker, after leading whitespace
    and a blockquote prefix, if any."""
    return _strip_blockquote_prefix(body).startswith(RESPOND_PR_OWNERSHIP_MARKER)


def _strip_blockquote_prefix(body: str) -> str:
    stripped = body.lstrip()
    return stripped[1:].lstrip() if stripped.startswith(">") else stripped


def classify_reply(body: str) -> tuple[str, str | None]:
    """(class, sha) for a marked reply's body. `sha` is the lowercased
    `commit_sha:` value, set only for REPLY_FIXED. The classes are tested in
    the order FIXED, fixed-without-sha, out-of-scope-or-deferred, not-fixed.
    The caller has already checked that the reply opens with the marker."""
    text = _strip_blockquote_prefix(body)
    if _FIXED_DISPOSITION_RE.search(text):
        sha_match = _COMMIT_SHA_VALUE_RE.search(text)
        if sha_match:
            return REPLY_FIXED, sha_match.group(1).lower()
        return REPLY_FIXED_WITHOUT_SHA, None
    if any(token in text for token in _DEFERRAL_FIELD_TOKENS):
        return REPLY_OUT_OF_SCOPE_OR_DEFERRED, None
    return REPLY_NOT_FIXED, None


def _parse_created_at(value: object) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _login_of(comment: dict) -> str | None:
    user = comment.get("user")
    login = user.get("login") if isinstance(user, dict) else None
    return login if isinstance(login, str) else None


def _pull_request_number(comment: dict) -> int | None:
    match = _PULL_REQUEST_URL_NUMBER_RE.search(str(comment.get("pull_request_url") or ""))
    return int(match.group(1)) if match else None


def _skip_reason(comment: dict, *, login: str, merged_branches: dict[int, str]) -> str | None:
    """Why `comment` is not a candidate source, or None when it is. The
    reasons key the skip counts `mine` prints."""
    comment_login = _login_of(comment)
    if comment_login is None or comment_login.casefold() != login.casefold():
        return "other-author"
    if comment.get("in_reply_to_id") is not None:
        return "reply"
    body = comment.get("body")
    if not isinstance(body, str) or not body.strip():
        return "empty-body"
    if _opens_with_marker(body):
        return "agent-reply"
    path = comment.get("path")
    if not isinstance(path, str) or not _is_repo_relative_path(path):
        return "malformed"
    if comment.get("side") != "RIGHT":
        return "left-side"
    original_line = comment.get("original_line")
    if not _is_plain_int(original_line) or original_line < 1:
        return "no-line"
    pr_number = _pull_request_number(comment)
    if pr_number is None or not _is_plain_int(comment.get("id")):
        return "malformed"
    if pr_number not in merged_branches:
        return "unmerged-pr"
    return None


@dataclass(frozen=True)
class _FixReply:
    sha_text: str  # 7 to 40 lowercase hex characters, as the reply wrote it


def _index_replies_by_parent(comments: list[dict]) -> dict[int, list[dict]]:
    replies_by_parent: dict[int, list[dict]] = {}
    for comment in comments:
        parent_id = comment.get("in_reply_to_id")
        if isinstance(parent_id, int):
            replies_by_parent.setdefault(parent_id, []).append(comment)
    return replies_by_parent


def _thread_outcome(comment: dict, replies: list[dict], *, login: str, stats: Counter) -> _FixReply | str:
    """The latest valid FIXED reply in `comment`'s thread, or the skip reason.

    A reply counts only when it is by `login` and opens with the ownership
    marker. The latest FIXED reply by `created_at` wins, and a later reply of
    another class does not displace it. Without a FIXED reply the reason is the
    class of the latest marked reply. A marked reply whose `created_at` does
    not parse cannot be ordered, so it is dropped and counted in `stats`."""
    marked: list[tuple[float, int, str, str | None]] = []
    for reply in replies:
        reply_login = _login_of(reply)
        body = reply.get("body")
        if (
            reply_login is None or reply_login.casefold() != login.casefold()
            or not isinstance(body, str) or not _opens_with_marker(body)
        ):
            continue
        created_ts = _parse_created_at(reply.get("created_at"))
        if created_ts is None:
            stats["reply-unparseable-created-at"] += 1
            continue
        reply_class, sha_text = classify_reply(body)
        reply_id = reply.get("id")
        marked.append((created_ts, reply_id if _is_plain_int(reply_id) else 0, reply_class, sha_text))
    if not marked:
        return "no-agent-reply"
    marked.sort(key=lambda entry: entry[:2])
    fixed_replies = [entry for entry in marked if entry[2] == REPLY_FIXED]
    if fixed_replies:
        return _FixReply(sha_text=fixed_replies[-1][3])
    return marked[-1][2]


# --- Commit resolution and anchor checks --------------------------------------


class GitExecutionError(Exception):
    """git gave no answer to a question about a comment: it timed out, could
    not start, died on a signal, or exited with a status the call has no
    meaning for. Distinct from a negative answer, which skips the comment for
    its content."""


def _run_git_status(
    repo_dir: Path, args: Sequence[str], *, answers: frozenset[int], config: Sequence[str] = (),
) -> subprocess.CompletedProcess:
    """The result of `git [-c <config>...] <args>` as bytes when its exit status
    is in `answers`. `args[0]` is the subcommand, which a GitExecutionError
    names. Raises GitExecutionError for a timeout, an unstartable git, a
    signal death, or any other status."""
    try:
        result = subprocess.run(
            ["git", *chain.from_iterable(("-c", setting) for setting in config), *args],
            cwd=repo_dir, capture_output=True, timeout=mine_review_rounds._LOCAL_GIT_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise GitExecutionError(f"git {args[0]}: {type(exc).__name__}") from exc
    if result.returncode not in answers:
        raise GitExecutionError(f"git {args[0]} exited {result.returncode}")
    return result


def _verified_object(repo_dir: Path, name: str) -> str | None:
    """The object id `name` resolves to, or None when `name` is no object.
    `--verify --quiet` exits 1, not 128, for a name that does not resolve."""
    result = _run_git_status(repo_dir, ["rev-parse", "--verify", "--quiet", name], answers=frozenset({0, 1}))
    return result.stdout.decode("utf-8", errors="replace").strip() if result.returncode == 0 else None


def _is_ancestor(repo_dir: Path, commit: str, ref: str) -> bool:
    """Whether `commit` is reachable from `ref`. An object the repo does not
    hold reads as not reachable. Raises GitExecutionError for any other failure."""
    result = _run_git_status(
        repo_dir, ["merge-base", "--is-ancestor", commit, ref], answers=frozenset({0, 1, _GIT_FATAL_STATUS}),
    )
    if result.returncode != _GIT_FATAL_STATUS:
        return result.returncode == 0
    if _verified_object(repo_dir, f"{commit}^{{commit}}") and _verified_object(repo_dir, f"{ref}^{{commit}}"):
        raise GitExecutionError(f"git merge-base exited {_GIT_FATAL_STATUS} for two commits that exist")
    return False


def _resolve_commit(repo_dir: Path, sha_text: str) -> str | None:
    """The full SHA that `sha_text` names as a commit, or None when it names
    none or is ambiguous."""
    full_sha = _verified_object(repo_dir, f"{sha_text}^{{commit}}")
    return full_sha if full_sha is not None and _SHA_RE.fullmatch(full_sha) else None


def _parent_commit(repo_dir: Path, commit: str) -> str | None:
    """The first parent of `commit`, or None for a root commit."""
    return _verified_object(repo_dir, f"{commit}^")


def _commit_date(repo_dir: Path, commit: str) -> str | None:
    result = _run_git_status(repo_dir, ["log", "-1", "--format=%aI", commit], answers=frozenset({0}))
    return result.stdout.decode("utf-8", errors="replace").strip() or None


def _pr_branch_commits(repo_dir: Path, branch: mine_review_rounds._BranchGit) -> frozenset[str] | None:
    """The commits the PR's own branch adds beyond its merge-base with
    origin/main, or None when they cannot be listed: no merge-base, or a failed
    `rev-list`. An empty listing also reads as None: the merge-base is the
    branch tip, so origin/main already holds the PR head and the branch has no
    commits of its own to test membership against. The caller records no branch
    membership then, and `confirm` labels it unknown."""
    if branch.ref is None or branch.merge_base is None:
        return None
    try:
        result = _run_git_status(
            repo_dir, ["rev-list", f"{branch.merge_base}..{branch.ref}"], answers=frozenset({0}),
        )
    except GitExecutionError as exc:
        print(f"mine-pr-comments: could not list the commits of {branch.ref} ({exc})", file=sys.stderr)
        return None
    return frozenset(result.stdout.decode("utf-8", errors="replace").split()) or None


def _normalize_whitespace(text: str) -> str:
    """Text with all whitespace removed, so a comparison agrees with the
    `-w` blame that ignores whitespace-only changes."""
    return "".join(text.split())


def _commented_line_text(diff_hunk: str) -> str:
    """The whitespace-normalized text of the line a comment is anchored to: the
    last line of its diff hunk, minus the diff prefix column. Empty when there
    is none, and for a removed line, which a RIGHT-side comment cannot anchor."""
    hunk_lines = [line for line in diff_hunk.rstrip("\n").split("\n") if not line.startswith("\\")]
    if not hunk_lines or hunk_lines[-1][:1] not in {"+", " "}:
        return ""
    return _normalize_whitespace(hunk_lines[-1][1:])


def _file_line_text(repo_dir: Path, commit: str, path: str, line_number: int) -> str | None:
    """The whitespace-normalized text of line `line_number` of `path` at
    `commit`, or None when the file or the line does not exist. Lines split on
    LF alone, as `git blame -L` counts them, so a lone CR does not shift the
    numbering."""
    name = f"{commit}:{path}"
    result = _run_git_status(repo_dir, ["show", name], answers=frozenset({0, _GIT_FATAL_STATUS}))
    if result.returncode == _GIT_FATAL_STATUS:
        if _verified_object(repo_dir, name) is None:
            return None
        raise GitExecutionError(f"git show exited {_GIT_FATAL_STATUS} for a path that exists")
    lines = result.stdout.split(b"\n")
    if not 1 <= line_number <= len(lines):
        return None
    return _normalize_whitespace(lines[line_number - 1].decode("utf-8", errors="replace"))


class BlamedLine(NamedTuple):
    commit: str
    source_path: str  # the path at `commit`, which `-M -C` can make differ from the blamed path
    source_line: int  # the line number at `commit`


def blame_commented_line(repo_dir: Path, commit: str, path: str, line_number: int) -> BlamedLine | None:
    """The one commit that introduced line `line_number` of `path` at `commit`,
    with the line's own path and number there. Uses the flags of
    `mine_szz._blame_range_shas`. None for a `filename` that git quoted, which
    a path with a control character gets. The caller has read that line of
    that path at `commit`, so a blame that fails raises GitExecutionError."""
    result = _run_git_status(
        repo_dir,
        ["blame", "-w", "-M", "-C", "--porcelain", "-L", f"{line_number},{line_number}", commit, "--", path],
        answers=frozenset({0}),
        config=("core.quotePath=false",),
    )
    header = None
    source_path = None
    for line in result.stdout.decode("utf-8", errors="replace").split("\n"):
        if line.startswith("\t"):  # the blamed line's own content ends the group
            break
        if header is None:
            header = _BLAME_HEADER_RE.fullmatch(line)
            if header is None:
                return None
        elif line.startswith("filename "):
            source_path = line.removeprefix("filename ")
    if header is None or not source_path or source_path.startswith('"'):
        return None
    return BlamedLine(commit=header.group(1), source_path=source_path, source_line=int(header.group(2)))


# --- Candidate construction ---------------------------------------------------


def _build_candidate(
    repo_dir: Path, comment: dict, fix_reply: _FixReply, *, pr_number: int,
    branch_git: mine_review_rounds._BranchGit, pr_branch_commits: frozenset[str] | None, stats: Counter,
) -> Candidate | None:
    """The candidate for one owner comment with a FIXED reply, or None after
    counting the reason in `stats`. Raises GitExecutionError when git cannot
    answer, which `mine` counts under `_GIT_ERROR_REASON`."""
    original_commit_id = comment.get("original_commit_id")
    if not isinstance(original_commit_id, str) or not _SHA_RE.fullmatch(original_commit_id):
        stats["malformed"] += 1
        return None
    if branch_git.ref is None:
        stats[branch_git.ref_status] += 1  # `pr-unknown` or `fetch-failed`
        return None
    # A force-pushed PR head no longer reaches the commit the comment was
    # made on, so the commented code is not on the branch being resolved.
    # A local branch named like the provider-supplied `head.ref` binds in place of the
    # PR head, so a candidate can drop here as unreachable that the PR head would keep;
    # accepted at current scale.
    if not _is_ancestor(repo_dir, original_commit_id, branch_git.ref):
        stats["original-commit-unreachable"] += 1
        return None

    comment_path = comment["path"]
    original_line = comment["original_line"]
    diff_hunk = comment.get("diff_hunk") if isinstance(comment.get("diff_hunk"), str) else ""
    commented_text = _commented_line_text(diff_hunk)
    if not commented_text or _file_line_text(repo_dir, original_commit_id, comment_path, original_line) != commented_text:
        stats["anchor-mismatch"] += 1
        return None

    blamed = blame_commented_line(repo_dir, original_commit_id, comment_path, original_line)
    base_commit = _parent_commit(repo_dir, blamed.commit) if blamed else None
    if blamed is None or base_commit is None:
        stats["head-unresolved"] += 1
        return None
    if _file_line_text(repo_dir, blamed.commit, blamed.source_path, blamed.source_line) != commented_text:
        stats["anchor-mismatch"] += 1
        return None

    fix_commit = _resolve_commit(repo_dir, fix_reply.sha_text)
    if fix_commit is None or not _is_ancestor(repo_dir, fix_commit, branch_git.ref):
        stats["fix-sha-unresolvable"] += 1
        return None
    # The fix commit must come after the head: `pin_defect_commits` pins only
    # the fix, so the head has to be reachable from it.
    if fix_commit == blamed.commit or not _is_ancestor(repo_dir, blamed.commit, fix_commit):
        stats["fix-not-descendant"] += 1
        return None
    fix_date = _commit_date(repo_dir, fix_commit)
    if fix_date is None:
        stats["fix-sha-unresolvable"] += 1
        return None

    body = comment["body"].replace("\r\n", "\n").replace("\r", "\n").strip()
    # The schema has no location field, so the description's prefix carries it.
    # The path and line are the head's: the tree the fixture and the judge see.
    description = f"{blamed.source_path}:{blamed.source_line} — {body}"
    disallowed = first_disallowed_character(description, allowed=STORED_DESCRIPTION_ALLOWED)
    if disallowed is not None:
        stats["invisible-characters" if is_invisible_character(disallowed) else "control-characters"] += 1
        return None
    evidence = {
        "path": blamed.source_path,
        "comment_path": comment_path,
        "pr_number": pr_number,
        "comment_id": comment["id"],
        "comment_url": comment.get("html_url"),
        "diff_hunk": diff_hunk,
        "original_commit_id": original_commit_id,
        "created_at": comment.get("created_at"),
        "public_comment_text": description,
    }
    if pr_branch_commits is not None:  # absent when the branch's commits could not be listed
        evidence["head_on_pr_branch"] = blamed.commit in pr_branch_commits
    return Candidate(
        id=f"pr-comment:{comment['id']}",
        source="pr-comment",
        lens=guess_lens(blamed.source_path),
        base_commit=base_commit,
        head_commit=blamed.commit,
        fix_commit=fix_commit,
        fix_date=fix_date,
        # Both anchor checks above passed, so the line exists at the head.
        lines_exist_at_introducing_head=True,
        reviewer_could_have_caught_it=True,
        file_is_markdown=is_markdown_path(blamed.source_path),
        ref_status=branch_git.ref_status,
        description=description,
        evidence=evidence,
    )


@dataclass
class _PullRequestGit:
    branch: mine_review_rounds._BranchGit
    commits: frozenset[str] | None


def mine(repo_dir: Path, *, run: GhRun = subprocess.run) -> list[Candidate]:
    """Mine PR-comment candidates from the authenticated owner's inline review
    comments on merged PRs, in this checkout's `origin` repository on
    github.com when the provider reports it public.

    `run` carries every `gh` call and is the test seam; git calls always run
    for real. A comment the miner cannot attribute is skipped and counted by
    reason, as in `mine_review_rounds.mine`. A body holding a joiner,
    variation selector, or other invisible character is counted as
    `invisible-characters`, and one holding any other control character as
    `control-characters`. A comment on which git gave no answer is skipped,
    counted under `git-error`, and listed by ID. The run completes and writes
    its shortlist, so a rerun retries that comment and rewrites the file, and
    one that fails again fails for that comment, not for the run. A run that
    skipped a comment on an unfetchable PR head exits 2 after printing the
    counts and writes nothing, because a partial shortlist would stand in for
    a complete one. See `_REF_STATUS_FETCH_FAILED` for the cause.
    """
    repository = resolve_origin_repository(repo_dir)
    require_public_repo(repo_dir, repository, run=run)
    login = discover_author_login(repo_dir, run=run)
    print(f"mine-pr-comments: reading {repository} as {login}", file=sys.stderr)
    stats: Counter = Counter()
    comments = fetch_review_comments(repo_dir, repository, stats=stats, run=run)
    merged_branches = fetch_merged_pr_branches(repo_dir, repository, stats=stats, run=run)
    replies_by_parent = _index_replies_by_parent(comments)

    candidates: list[Candidate] = []
    # Opened on a PR's first comment that has a FIXED reply, so a PR with none
    # is never fetched.
    pull_request_git: dict[int, _PullRequestGit] = {}
    for comment in comments:
        reason = _skip_reason(comment, login=login, merged_branches=merged_branches)
        if reason is not None:
            stats[reason] += 1
            continue
        outcome = _thread_outcome(comment, replies_by_parent.get(comment["id"], []), login=login, stats=stats)
        if isinstance(outcome, str):
            stats[outcome] += 1
            continue
        pr_number = _pull_request_number(comment)
        if pr_number not in pull_request_git:
            # `head.ref` is provider-supplied: a name equal to a local branch binds that branch
            # in place of the PR head. A candidate can then drop as unreachable, or be kept with
            # `ref_status` "local-branch" and a `head_on_pr_branch` computed from that branch.
            branch_git = mine_review_rounds.open_branch_git(
                repo_dir, mine_review_rounds.resolve_branch_ref(repo_dir, merged_branches[pr_number], pr_number),
            )
            pull_request_git[pr_number] = _PullRequestGit(branch_git, _pr_branch_commits(repo_dir, branch_git))
        try:
            candidate = _build_candidate(
                repo_dir, comment, outcome, pr_number=pr_number, branch_git=pull_request_git[pr_number].branch,
                pr_branch_commits=pull_request_git[pr_number].commits, stats=stats,
            )
        except GitExecutionError as exc:
            print(f"mine-pr-comments: comment {comment['id']} skipped, {exc}", file=sys.stderr)
            stats[_GIT_ERROR_REASON] += 1
            continue
        if candidate is not None:
            candidates.append(candidate)

    # Fails loudly on any id collision, rather than let confirm's lookups
    # silently resolve it to the wrong sibling.
    assert_unique_ids(candidates, miner="mine-pr-comments")

    print(
        f"mine-pr-comments: {len(candidates)} candidate(s) from {len(comments)} comment(s); "
        f"skipped {dict(sorted(stats.items()))}",
        file=sys.stderr,
    )
    if stats[_GIT_ERROR_REASON]:
        print(
            f"mine-pr-comments: {stats[_GIT_ERROR_REASON]} comment(s) skipped on a git failure, "
            "their IDs are listed above; a rerun retries them, and an ID that repeats "
            "fails for that comment, not for the run",
            file=sys.stderr,
        )
    if stats[_REF_STATUS_FETCH_FAILED]:
        unfetched_pr_numbers = sorted(
            pr_number for pr_number, pr_git in pull_request_git.items()
            if pr_git.branch.ref_status == _REF_STATUS_FETCH_FAILED
        )
        _fail(
            f"{stats[_REF_STATUS_FETCH_FAILED]} comment(s) skipped: PR head(s) "
            f"{', '.join(f'#{pr_number}' for pr_number in unfetched_pr_numbers)} could not be fetched "
            "(git's error for each is above) -- nothing written; a rerun clears a network, auth, "
            "or ref-lock failure, not a pull ref the remote lacks or a fetch that exceeds the "
            f"{mine_review_rounds._GIT_FETCH_TIMEOUT_S:g}s fetch timeout, which scales with the head's size"
        )
    return candidates
