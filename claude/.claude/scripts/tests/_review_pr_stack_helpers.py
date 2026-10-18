"""The `gh pr list` test double for the review-pr stack walk.

Consumers: test_review_pr_stack_helpers.py (the double's own tests),
test_review_pr_lib.py, and the acquire, checkout and diff script suites. Each
gh shim, including the one write_gh_pr_list_shim writes, imports this module
through a `sys.path` insert of this directory, so it uses only the standard
library and no relative import.
"""
from __future__ import annotations

import json
import shlex
import shutil
import stat
import sys
import textwrap
import time
from pathlib import Path

# The shim's exit status for a call the double does not model, matching the script suites' gh shims.
UNMODELED_CALL_EXIT_STATUS = 97

# `gh pr list --json` fields the double knows. A complete item (see pr_list_item) carries every one.
KNOWN_JSON_FIELDS = frozenset({
    "number", "title", "author", "headRefName", "baseRefName", "isCrossRepository", "reviews",
})


def pr_list_item(
    number: int,
    head_ref_name: str,
    base_ref_name: str,
    *,
    title: str = "A change",
    author_login: str | None = "alice",
    author_is_bot: bool = False,
    is_cross_repository: bool = False,
    reviews: list[dict] | None = None,
) -> dict:
    """One `gh pr list --json` item in gh's shape, carrying every field a lookup requests.

    `author_login=None` gives the null author gh reports for a deleted account.
    A bot PR author is `{"is_bot": true, "login": "app/<name>"}`, so pass the login with its `app/` prefix.
    """
    author = None if author_login is None else {"is_bot": author_is_bot, "login": author_login}
    return {
        "number": number,
        "title": title,
        "author": author,
        "headRefName": head_ref_name,
        "baseRefName": base_ref_name,
        "isCrossRepository": is_cross_repository,
        "reviews": [] if reviews is None else reviews,
    }


def pr_list_review(
    author_login: str | None,
    state: str = "APPROVED",
    *,
    association: str = "MEMBER",
    submitted_at: str | None = "2026-01-01T00:00:00Z",
    body: str = "",
) -> dict:
    """One review as `gh pr list --json reviews` gives it: the author is `{"login": ...}` only, with no bot marker.

    `author_login=None` gives the null author of a deleted account.
    A bot reviewer's login has no `[bot]` suffix and its association reads CONTRIBUTOR, as gh prints it.
    `submitted_at=None` gives the null a PENDING review carries.
    """
    return {
        "author": None if author_login is None else {"login": author_login},
        "authorAssociation": association,
        "state": state,
        "submittedAt": submitted_at,
        "body": body,
    }


def _usage_failure(message: str) -> int:
    print(f"gh pr list double: {message}", file=sys.stderr)
    return UNMODELED_CALL_EXIT_STATUS


def _parse_pr_list_args(args: list[str]) -> dict[str, str] | str:
    """The flag values of an accepted call, or the reason the call is not modeled.

    Accepted: `-R <owner/repo>`, a single-token `--head=<branch>` with a non-empty branch, `--state open`,
    `--limit <n>` and `--json <fields>`, each exactly once and in any order.
    An empty `--head=` is refused because gh answers it with every open PR, a reply the double does not model.
    """
    flags: dict[str, str] = {}
    remaining = list(args)
    while remaining:
        token = remaining.pop(0)
        if token.startswith("--head="):
            name, value = "--head", token[len("--head="):]
        elif token in ("-R", "--state", "--limit", "--json"):
            if not remaining:
                return f"{token} has no value"
            name, value = token, remaining.pop(0)
        else:
            return f"unmodeled argument {token!r} (a two-token --head is not modeled)"
        if name in flags:
            return f"{name} given twice"
        flags[name] = value
    missing = [name for name in ("-R", "--head", "--state", "--limit", "--json") if name not in flags]
    if missing:
        return "missing " + ", ".join(missing)
    if flags["-R"].count("/") != 1 or not all(flags["-R"].split("/")):
        return f"-R {flags['-R']!r} is not <owner>/<repo>"
    if flags["--head"] == "":
        return "--head= has an empty value"
    if flags["--state"] != "open":
        return f"--state {flags['--state']!r} is not 'open'"
    if not flags["--limit"].isdigit() or int(flags["--limit"]) < 1:
        return f"--limit {flags['--limit']!r} is not a positive integer"
    return flags


def answer_gh_pr_list(args: list[str], replies: dict) -> int:
    """Answer one `gh pr list` call, returning its exit status.

    `args` is the argv after `gh pr list`. The call is accepted only in the form the stack walk issues
    (see _parse_pr_list_args). Anything else prints a stderr line and returns 97.

    `replies` maps an exact `--head=` value to a reply:
    - A list: the complete PR items (see pr_list_item) for that branch, each projected to the requested `--json` fields.
      Every item is returned, so `--limit` is validated but never applied: a reply of more items than the limit
      reaches the caller whole.
    - A dict: `"sleep": seconds` is slept first, then `"stderr": text` is printed to stderr, then `"stdout": text`
      is written to stdout, then the call exits with `"exit": N` (0 when absent).
    A branch with no key answers `[]`, GitHub's reply for a branch with no open PR.

    A requested field outside KNOWN_JSON_FIELDS prints `Unknown JSON field: <name>` on stderr and returns 1.
    The double writes no call log; the shim that wraps it owns that.
    """
    parsed = _parse_pr_list_args(args)
    if isinstance(parsed, str):
        return _usage_failure(parsed)
    flags = parsed
    requested_fields = flags["--json"].split(",")
    for field in requested_fields:
        if field not in KNOWN_JSON_FIELDS:
            print(f"Unknown JSON field: {field}", file=sys.stderr)
            return 1

    head_branch = flags["--head"]
    reply = replies.get(head_branch, [])

    if isinstance(reply, dict):
        if sleep_seconds := reply.get("sleep"):
            time.sleep(sleep_seconds)
        if "stderr" in reply:
            print(reply["stderr"], file=sys.stderr, end="")
        if "stdout" in reply:
            sys.stdout.write(reply["stdout"])
            sys.stdout.flush()
        return int(reply.get("exit", 0))

    if not isinstance(reply, list):
        return _usage_failure(f"reply for --head={head_branch} is neither a list nor a dict")
    projected = []
    for item in reply:
        if not isinstance(item, dict) or any(field not in item for field in requested_fields):
            return _usage_failure(
                f"reply item for --head={head_branch} is not a complete pr_list_item holding {flags['--json']}"
            )
        projected.append({field: item[field] for field in requested_fields})
    print(json.dumps(projected))
    return 0


# The PR numbers stack_replies gives its two PRs.
STACK_LOWER_PR_NUMBER = 41
STACK_UPPER_PR_NUMBER = 43


def stack_replies(stacked_branch: str, lower_branch: str, *, lower_reviewed: bool) -> dict:
    """`gh pr list` replies for a two-level stack: the upper PR (reviewed) has head `stacked_branch` and base `lower_branch`,
    and the lower PR has head `lower_branch` and base main, with a review only when `lower_reviewed`."""
    reviews = [pr_list_review("reviewer")]
    return {
        stacked_branch: [pr_list_item(STACK_UPPER_PR_NUMBER, stacked_branch, lower_branch, reviews=reviews)],
        lower_branch: [pr_list_item(STACK_LOWER_PR_NUMBER, lower_branch, "main", reviews=reviews if lower_reviewed else [])],
    }


def pr_list_calls(calls: list[list[str]]) -> list[list[str]]:
    """The `gh pr list` calls among a suite's logged argv lists."""
    return [call for call in calls if call[:2] == ["pr", "list"]]


def pr_list_head_values(calls: list[list[str]]) -> list[str]:
    """The `--head=` value of each `gh pr list` call among a suite's logged argv lists, in call order."""
    return [token.removeprefix("--head=") for call in pr_list_calls(calls) for token in call if token.startswith("--head=")]


def write_gh_pr_list_shim(bin_dir: Path, call_log: Path, replies: dict) -> Path:
    """Write an executable `gh` into `bin_dir` for a suite that calls the lib directly.

    The shim appends each call's argv to `call_log` as one `{"args": [...]}` JSON line, answers
    `gh pr list` through answer_gh_pr_list with `replies`, and exits 97 on any other call.
    Returns the shim's path.
    """
    shim_path = bin_dir / "gh"
    shim_path.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json
        import sys

        sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})
        from _review_pr_stack_helpers import UNMODELED_CALL_EXIT_STATUS, answer_gh_pr_list

        REPLIES = json.loads({json.dumps(replies)!r})
        args = sys.argv[1:]
        with open({str(call_log)!r}, "a") as call_log_file:
            call_log_file.write(json.dumps({{"args": args}}) + chr(10))
        if args[:2] == ["pr", "list"]:
            sys.exit(answer_gh_pr_list(args[2:], REPLIES))
        print("gh shim: unmodeled call: " + " ".join(args), file=sys.stderr)
        sys.exit(UNMODELED_CALL_EXIT_STATUS)
    """))
    shim_path.chmod(shim_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return shim_path


def logged_gh_calls(call_log: Path) -> list[list[str]]:
    """The argv of each call write_gh_pr_list_shim logged, in order; empty when none was made."""
    if not call_log.exists():
        return []
    return [json.loads(line)["args"] for line in call_log.read_text().splitlines() if line.strip()]


def copy_script_with_stubbed_walk(script_path: Path, sandbox_dir: Path, walk_stdout: str) -> Path:
    """Copy `script_path` and its sourced libraries into `sandbox_dir`, with `review_pr_walk_stack` redefined to print
    `walk_stdout` and return 0, and return the copy's path.

    The scripts source `../hooks/_lib.sh` and `_review-pr-lib.sh` relative to themselves, so the sandbox holds a
    `scripts/` copy and a `hooks/` symlink to the real directory.
    A script suite uses the copy to drive a walk output the real walk never prints.
    """
    source_dir = script_path.parent
    sandbox_scripts_dir = sandbox_dir / "scripts"
    sandbox_scripts_dir.mkdir(parents=True)
    shutil.copy2(script_path, sandbox_scripts_dir / script_path.name)
    walk_stub = f"\nreview_pr_walk_stack() {{ printf '%s' {shlex.quote(walk_stdout)}; }}\n"
    (sandbox_scripts_dir / "_review-pr-lib.sh").write_text((source_dir / "_review-pr-lib.sh").read_text() + walk_stub)
    (sandbox_dir / "hooks").symlink_to(source_dir.parent / "hooks")
    return sandbox_scripts_dir / script_path.name
