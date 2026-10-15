#!/usr/bin/env python3
"""Prints the facts /plan-it Step 7's push question needs, plus the exact push and share commands.

Usage: plan-push-preflight.py <repo-relative path under .claude/plans/ ending in .md>

It reads git state locally and never pushes. It sends `gh` requests only when the
push URL's host is `github.com` or the host `GH_HOST` names, and always names that
host. It reads `GH_HOST` from its own environment only, with no direnv resync.
Its URL parser is deliberately stricter than `_lib.sh`'s `_lib_origin_owner_repo`
and `ci-watch.sh`'s host parsing: any push URL it cannot parse without loss stops
the offer.

Output: `KEY: value` lines on stdout, all written in one write after the decision.
- `OFFER: ask` or `OFFER: stop` appears exactly once.
- A stop adds one `STOP_REASON: <code>: <sentence>` line.
- An ask adds `FACT: ...` lines in question order, then `PUSH_COMMAND: ...`, then
  `SHARE_COMMAND: ...` when the push URL is on a gh host and `gh` is on PATH.
- Every value has control, format, and line-separator characters removed.

Stop codes: not-on-branch, branch-name, tip, plan-not-in-tip, remote-name,
push-url-count, push-url-form, push-url-path, default-branch, open-pr, usage,
internal-error.

Exit status: 0 when the script reached a decision (ask or stop), 1 on an internal
error (the `internal-error` stop line carries only the exception's type name), and
2 on a usage error. Every exit with one of these statuses prints an `OFFER` line, and
nothing on stderr, while stdout is writable. A run ended by a signal prints no `OFFER` line.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import unicodedata
import urllib.parse
from typing import NamedTuple

_PLAN_PATH_PATTERN = r"\.claude/plans/[A-Za-z0-9][A-Za-z0-9._-]*\.md"
_BRANCH_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._/-]*"
_REMOTE_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]*"
_HOST_PATTERN = r"[a-z0-9_]([a-z0-9_.-]*[a-z0-9_])?"
_OWNER_PATTERN = r"[A-Za-z0-9_][A-Za-z0-9_.-]*"
# A name made only of dots reads as a path step in a URL.
_REPO_PATTERN = r"(?!\.+\Z)[A-Za-z0-9_.][A-Za-z0-9_.-]*"
# A SHA-1 object name is 40 hex digits and a SHA-256 object name is 64.
_SHA_PATTERN = r"[0-9a-f]{40}|[0-9a-f]{64}"
_RAW_URL_PATTERN = r"[!-~]+"
_RAW_URL_FORBIDDEN_CHARACTERS = "\\?#"
# Git's scp-like form: `[user@]host:path`, with no `/` before the first `:`.
_SCP_LIKE_PATTERN = r"(?:(?P<user>[^@/:]+)@)?(?P<host>[^@/:]+):(?P<path>.*)"

_OUTPUT_KEYS = frozenset({"OFFER", "STOP_REASON", "FACT", "PUSH_COMMAND", "SHARE_COMMAND"})
_ACCEPTED_URL_SCHEMES = frozenset({"https", "http", "ssh", "git", "file"})
_GITHUB_HOST = "github.com"
# A display cap that keeps the question short. The count line reports the remainder.
_SUBJECT_CAP = 10
# git-config(1) --get: "Returns error code 1 if the key was not found."
_GIT_CONFIG_KEY_NOT_FOUND_RETURNCODE = 1
_EXIT_DECIDED = 0
_EXIT_INTERNAL_ERROR = 1
_EXIT_USAGE = 2
_STRIPPED_GH_ENV_VARS = frozenset({"GH_HOST", "GH_REPO"})

_USAGE_SENTENCE = "plan-push-preflight.py <repo-relative path under .claude/plans/ ending in .md>"
_UNKNOWN_COMMITS_FACT = "Commits since the baseline: unknown, and may be the branch's whole history"

class StopOffer(Exception):
    """A named reason the push offer stops, carried to main as an `OFFER: stop` result."""

    def __init__(self, code: str, sentence: str) -> None:
        super().__init__(code)
        self.code = code
        self.sentence = sentence


class PushUrl(NamedTuple):
    host: str | None
    owner: str
    repo: str
    display: str


def _fullmatches(pattern: str, value: str) -> bool:
    # fullmatch, not a `$`-anchored match, because `$` accepts a trailing newline.
    return re.fullmatch(pattern, value) is not None


def _valid_plan_path(value: str) -> bool:
    return _fullmatches(_PLAN_PATH_PATTERN, value)


def _valid_branch_name(value: str) -> bool:
    return _fullmatches(_BRANCH_PATTERN, value)


def _valid_remote_name(value: str) -> bool:
    return _fullmatches(_REMOTE_PATTERN, value)


def _valid_host(value: str) -> bool:
    return _fullmatches(_HOST_PATTERN, value)


def _valid_owner(value: str) -> bool:
    return _fullmatches(_OWNER_PATTERN, value)


def _valid_repo(value: str) -> bool:
    return _fullmatches(_REPO_PATTERN, value)


def _sanitize(text: str) -> str:
    """Drops control, format, and line-separator characters, and folds other space separators to U+0020.

    `str.splitlines()` splits on U+2028 and U+2029 (categories Zl and Zp), so they go too.
    """
    kept = []
    for character in text:
        category = unicodedata.category(character)
        if category.startswith("C") or category in ("Zl", "Zp"):
            continue
        kept.append(" " if category == "Zs" else character)
    return "".join(kept)


def _line(key: str, value: str) -> str:
    """The only builder of output lines, so no source of text can skip the sanitizer."""
    if key not in _OUTPUT_KEYS:
        raise RuntimeError("not an output key")
    return key + ": " + _sanitize(value)


def _output_value(text: str) -> str:
    return text.removesuffix("\n")


def _output_lines(text: str) -> list[str]:
    """Splits on "\\n" only. Empty output is zero lines, where "".split("\\n") would be one."""
    body = text.removesuffix("\n")
    if body == "":
        return []
    return body.split("\n")


def _emit(lines: list[str]) -> None:
    # One write of UTF-8 bytes, so an encoding error never leaves a partial ask on stdout.
    sys.stdout.buffer.write(("\n".join(lines) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def _run(argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    """The only place a child process starts. Child stderr is captured and never relayed.

    Output is decoded here, not by text mode, because text mode turns a lone CR or CRLF into "\\n".
    """
    completed = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, capture_output=True, check=False)
    return subprocess.CompletedProcess(
        argv, completed.returncode, stdout=completed.stdout.decode("utf-8", errors="replace"), stderr=""
    )


def _git(*args: str) -> subprocess.CompletedProcess:
    # GIT_NO_LAZY_FETCH keeps a read of a missing object from fetching, on a git that honors it.
    env = {**os.environ, "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1"}
    return _run(["git", *args], env)


def _gh(*args: str) -> subprocess.CompletedProcess:
    # Without GH_HOST and GH_REPO, the host in the qualified name is the only host gh sees.
    env = {name: value for name, value in os.environ.items() if name not in _STRIPPED_GH_ENV_VARS}
    return _run(["gh", *args], env)


def _raw_url_ok(raw: str) -> bool:
    return _fullmatches(_RAW_URL_PATTERN, raw) and not any(
        character in raw for character in _RAW_URL_FORBIDDEN_CHARACTERS
    )


def _push_url_form_stop() -> StopOffer:
    return StopOffer("push-url-form", "the push URL is not a form this script parses without loss.")


def _split_owner_repo(path: str) -> tuple[str, str]:
    # Removes at most one leading slash, one trailing slash, and one `.git`, so `//o/r` and `o/r//` stop.
    segments = path.removeprefix("/").removesuffix("/").removesuffix(".git").split("/")
    if len(segments) != 2 or not _valid_owner(segments[0]) or not _valid_repo(segments[1]):
        raise StopOffer("push-url-path", "the push URL's path is not exactly OWNER/REPO.")
    return segments[0], segments[1]


def _parse_scheme_url(raw: str) -> PushUrl:
    parts = urllib.parse.urlsplit(raw)
    if parts.scheme not in _ACCEPTED_URL_SCHEMES:
        raise _push_url_form_stop()
    if parts.scheme == "file":
        if parts.netloc != "" or "file://" + parts.path != raw:
            raise _push_url_form_stop()
        owner, repo = _split_owner_repo(parts.path)
        return PushUrl(None, owner, repo, raw)
    userinfo, at_sign, _ = parts.netloc.rpartition("@")
    host = parts.hostname
    if "@" in userinfo or not host or not _valid_host(host):
        raise _push_url_form_stop()
    port_text = "" if parts.port is None else ":" + str(parts.port)
    # urlsplit normalizes case, an empty port, and zero-padded ports, so a rebuild that differs is lossy.
    if parts.scheme + "://" + userinfo + at_sign + host + port_text + parts.path != raw:
        raise _push_url_form_stop()
    owner, repo = _split_owner_repo(parts.path)
    return PushUrl(host, owner, repo, parts.scheme + "://" + host + port_text + parts.path)


def _parse_scp_like_or_local_url(raw: str) -> PushUrl:
    match = re.fullmatch(_SCP_LIKE_PATTERN, raw)
    if match is None:
        owner, repo = _split_owner_repo(raw)
        return PushUrl(None, owner, repo, raw)
    host = match["host"].lower()
    if not _valid_host(host):
        raise _push_url_form_stop()
    owner, repo = _split_owner_repo(match["path"])
    return PushUrl(host, owner, repo, raw[match.start("host") :])


def _parse_push_url(raw: str) -> PushUrl:
    """Classifies the push URL in a fixed order and fails closed on any loss."""
    try:
        if not _raw_url_ok(raw) or "::" in raw:
            raise _push_url_form_stop()
        if "://" in raw:
            return _parse_scheme_url(raw)
        return _parse_scp_like_or_local_url(raw)
    except ValueError:
        # A urlsplit ValueError can carry the whole netloc, userinfo included, so its text is dropped.
        raise _push_url_form_stop() from None


def _is_gh_host(host: str | None, gh_host_value: str | None) -> bool:
    if host is None:
        return False
    if host == _GITHUB_HOST:
        return True
    if gh_host_value is None:
        return False
    normalized = gh_host_value.strip().lower()
    return _valid_host(normalized) and normalized == host


def _read_branch() -> str:
    completed = _git("symbolic-ref", "--quiet", "HEAD")
    head_ref = _output_value(completed.stdout)
    if completed.returncode != 0 or not head_ref.startswith("refs/heads/"):
        raise StopOffer("not-on-branch", "HEAD is not on a local branch.")
    branch = head_ref.removeprefix("refs/heads/")
    if not _valid_branch_name(branch):
        raise StopOffer(
            "branch-name",
            "the branch name has a character outside A-Z a-z 0-9 . _ / -, or does not start with a letter or digit.",
        )
    return branch


def _resolve_tip(branch: str) -> str:
    completed = _git("rev-parse", "--verify", "--quiet", "refs/heads/" + branch + "^{commit}")
    sha = _output_value(completed.stdout)
    if completed.returncode != 0 or not _fullmatches(_SHA_PATTERN, sha):
        raise StopOffer("tip", "refs/heads/" + branch + " does not resolve to a commit.")
    return sha


def _require_plan_in_tip(sha: str, branch: str, plan_path: str) -> None:
    if _git("cat-file", "-e", sha + ":" + plan_path).returncode != 0:
        raise StopOffer(
            "plan-not-in-tip",
            plan_path + " is not in the commit at refs/heads/" + branch + ". Commit the plan first.",
        )


def _read_push_remote(branch: str) -> str:
    for key in ("branch." + branch + ".pushRemote", "remote.pushDefault", "branch." + branch + ".remote"):
        completed = _git("config", "--get", key)
        if completed.returncode == 0:
            remote = _output_value(completed.stdout)
            break
    else:
        remote = "origin"
    if not _valid_remote_name(remote):
        raise StopOffer(
            "remote-name",
            "the push remote's name has a character outside A-Z a-z 0-9 . _ -, or does not start with a letter or digit.",
        )
    return remote


def _read_single_push_url(remote: str) -> PushUrl:
    completed = _git("remote", "get-url", "--push", "--all", remote)
    urls = _output_lines(completed.stdout) if completed.returncode == 0 else []
    if len(urls) != 1:
        raise StopOffer(
            "push-url-count",
            "remote " + remote + " has " + str(len(urls)) + " push URLs, and the offer needs exactly one.",
        )
    return _parse_push_url(urls[0])


def _read_default_branch(remote: str) -> str | None:
    completed = _git("symbolic-ref", "--quiet", "refs/remotes/" + remote + "/HEAD")
    target = _output_value(completed.stdout)
    prefix = "refs/remotes/" + remote + "/"
    if completed.returncode != 0 or not target.startswith(prefix) or target == prefix:
        return None
    return target.removeprefix(prefix)


class CommitRange(NamedTuple):
    count: int
    subjects: list[str]


def _read_commit_range(remote: str, sha: str) -> CommitRange | None:
    """The commits `sha` adds past the remote's default branch, or None when git cannot say."""
    range_spec = "refs/remotes/" + remote + "/HEAD.." + sha
    count_result = _git("rev-list", "--count", range_spec)
    count_text = _output_value(count_result.stdout)
    if count_result.returncode != 0 or not count_text.isdecimal():
        return None
    log_result = _git(
        "log",
        "--no-decorate",
        "--no-show-signature",
        "--no-color",
        "--encoding=UTF-8",
        "--format=%h %s",
        "-n",
        str(_SUBJECT_CAP),
        range_spec,
    )
    if log_result.returncode != 0:
        return None
    return CommitRange(int(count_text), _output_lines(log_result.stdout))


def _pr_exists(branch: str, gh_repo_name: str) -> bool:
    return _gh("pr", "view", branch, "--repo", gh_repo_name, "--json", "number").returncode == 0


def _read_visibility(gh_repo_name: str) -> str | None:
    completed = _gh("repo", "view", gh_repo_name, "--json", "visibility", "--jq", ".visibility")
    visibility = _output_value(completed.stdout)
    return visibility if completed.returncode == 0 and visibility != "" else None


def _baseline_facts(remote: str, default_branch: str | None, branch: str) -> list[str]:
    if default_branch is None:
        return [
            "Baseline: unknown, because refs/remotes/" + remote + "/HEAD does not name a branch",
            "Default-branch check: did not run, so refs/heads/" + branch + " may be the remote's default branch",
        ]
    return ["Baseline: refs/remotes/" + remote + "/" + default_branch + " as of this clone's last fetch"]


def _commit_facts(commit_range: CommitRange | None) -> list[str]:
    if commit_range is None:
        return [_UNKNOWN_COMMITS_FACT]
    facts = ["Commits since the baseline: " + str(commit_range.count)]
    facts.extend("  " + subject for subject in commit_range.subjects)
    if commit_range.count > _SUBJECT_CAP:
        facts.append("  and " + str(commit_range.count - _SUBJECT_CAP) + " more")
    return facts


def _config_fact(label: str, key: str) -> str | None:
    """The fact line for one setting, or None when git reports the key unset.

    Any other failure reads as unknown, never as unset, because the setting changes what the push does.
    """
    completed = _git("config", "--get", key)
    if completed.returncode == 0:
        value = _output_value(completed.stdout)
        # `git config --get` prints an empty line, with exit status 0, for a key set to "" or with no `=`.
        if value == "":
            return label + ": " + key + " is set with an empty value or no value"
        return label + ": " + key + " is " + value
    if completed.returncode == _GIT_CONFIG_KEY_NOT_FOUND_RETURNCODE:
        return None
    return label + ": " + key + " could not be read (unknown)"


def _config_facts(remote: str) -> list[str]:
    facts = [
        _config_fact("Signed push", "push.gpgSign"),
        _config_fact("Receive program", "remote." + remote + ".receivepack"),
    ]
    return [fact for fact in facts if fact is not None]


def _gh_facts(branch: str, push_url: PushUrl, plan_path: str) -> tuple[list[str], str | None]:
    """The visibility and open-PR facts, plus the share command when one applies.

    Stops with `open-pr` when a PR exists for the branch.
    """
    on_gh_host = _is_gh_host(push_url.host, os.environ.get("GH_HOST"))
    gh_found = shutil.which("gh") is not None
    if not on_gh_host or not gh_found:
        reason = "the push URL is not on github.com or GH_HOST" if not on_gh_host else "gh was not found on PATH"
        return (
            [
                "Visibility: unknown, treated as public",
                "Open-PR check: did not run (" + reason + "), so a PR for this branch may already exist",
            ],
            None,
        )
    gh_repo_name = push_url.host + "/" + push_url.owner + "/" + push_url.repo
    if _pr_exists(branch, gh_repo_name):
        raise StopOffer("open-pr", "a PR exists for " + branch + ". Run /ready-for-review before any push.")
    visibility = _read_visibility(gh_repo_name) if push_url.host == _GITHUB_HOST else None
    facts = [
        "Visibility: " + (visibility if visibility is not None else "unknown, treated as public"),
        "Open-PR check: gh did not report a PR for this branch (a gh error also reads as none)",
    ]
    share_command = shlex.join(
        ["gh", "browse", plan_path, "--repo", gh_repo_name, "--branch", branch, "--no-browser"]
    )
    return facts, share_command


def _decide(argv: list[str]) -> list[str]:
    if len(argv) != 1 or not _valid_plan_path(argv[0]):
        raise StopOffer("usage", _USAGE_SENTENCE)
    plan_path = argv[0]

    branch = _read_branch()
    sha = _resolve_tip(branch)
    _require_plan_in_tip(sha, branch, plan_path)
    remote = _read_push_remote(branch)
    push_url = _read_single_push_url(remote)

    default_branch = _read_default_branch(remote)
    if default_branch == branch:
        raise StopOffer("default-branch", branch + " is the remote's default branch.")
    commit_range = None if default_branch is None else _read_commit_range(remote, sha)
    config_facts = _config_facts(remote)
    gh_facts, share_command = _gh_facts(branch, push_url, plan_path)

    facts = [
        "Destination: " + push_url.display + " (remote " + remote + ", its only push URL)",
        "Remote branch: refs/heads/" + branch + ", set to " + sha,
        *_baseline_facts(remote, default_branch, branch),
        *_commit_facts(commit_range),
        *gh_facts,
        *config_facts,
    ]
    push_command = shlex.join(
        [
            "git",
            "-c",
            "push.pushOption=",
            "push",
            "--no-follow-tags",
            "--no-recurse-submodules",
            remote,
            sha + ":refs/heads/" + branch,
        ]
    )
    lines = [_line("OFFER", "ask"), *(_line("FACT", fact) for fact in facts), _line("PUSH_COMMAND", push_command)]
    if share_command is not None:
        lines.append(_line("SHARE_COMMAND", share_command))
    return lines


def main(argv: list[str]) -> int:
    try:
        lines = _decide(argv)
        status = _EXIT_DECIDED
    except StopOffer as stop:
        lines = [_line("OFFER", "stop"), _line("STOP_REASON", stop.code + ": " + stop.sentence)]
        status = _EXIT_USAGE if stop.code == "usage" else _EXIT_DECIDED
    except Exception as error:
        # Only the type name is printed: an exception's text can carry the push URL's userinfo.
        lines = [_line("OFFER", "stop"), _line("STOP_REASON", "internal-error: " + type(error).__name__)]
        status = _EXIT_INTERNAL_ERROR
    _emit(lines)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
