"""Tests for review-pr-acquire.sh -- /review-pr's acquire step, replacing a
hand-typed sequence of `gh` calls with one script that owns its own
pagination reconciliation for the 100-entry caps `gh pr view --json`
carries on `files` and `commits`.

The `gh` CLI is replaced by a PATH shim that records every invocation it
receives, matching the sibling review-pr script test files' own shim
pattern. No git repo is needed -- this script makes no local git calls.
"""
from __future__ import annotations

import json
import re
import subprocess
import textwrap
from pathlib import Path

import pytest
from helpers import SCRIPTS_DIR

from .conftest import _seed_session, _shimmed_env

SCRIPT = SCRIPTS_DIR / "review-pr-acquire.sh"
OWNER_REPO = "foo/bar"
PR_NUMBER = "42"
PR_IDENTITY = f"{OWNER_REPO}#{PR_NUMBER}"
SID = "test-session-review-pr-acquire"

# gh api's own default-method rule (`gh api --help`): GET unless a field flag
# is present, in which case the default flips to POST.
_WRITE_METHOD_FLAGS = ("-X", "--method")
_WRITE_FIELD_FLAGS = ("-f", "-F", "--field", "--raw-field")
_KNOWN_API_ENDPOINT = re.compile(
    rf"repos/{re.escape(OWNER_REPO)}/pulls/{re.escape(PR_NUMBER)}(/(files|commits|reviews))?$"
)


def _flag_name(token: str) -> str:
    """"--method=GET" and "--method" "GET" are both valid gh/cobra flag
    syntax -- strip a glued "=value" suffix so a flag-name comparison
    catches either form."""
    return token.split("=", 1)[0]


def _assert_gh_calls_are_read_only(calls: list[list[str]]) -> None:
    """Allowlists the exact `gh` call shapes review-pr-acquire.sh's header
    comment documents it as making: `gh pr view`, `gh pr checks`, and `gh
    api` against its own known GET endpoints (the bare pulls/{N} resource
    plus its files/commits/reviews sub-resources), none carrying a method
    or field flag that would flip `gh api`'s default method to POST. Any
    call outside that allowlist fails, including an unanticipated write
    shape this suite's fixtures never modeled.

    Covers only the `gh` invocations the shimmed code paths this suite's
    fixtures drive actually make; it says nothing about a non-`gh` write
    (e.g. a raw `curl`) review-pr-acquire.sh might issue.
    """
    for args in calls:
        if args[:2] == ["pr", "view"]:
            assert "-R" in args and "--json" in args, f"gh pr view missing expected flags: {args}"
            continue
        if args[:2] == ["pr", "checks"]:
            assert "-R" in args and "--json" in args, f"gh pr checks missing expected flags: {args}"
            continue
        if args[:1] == ["api"]:
            endpoint = args[1] if len(args) > 1 else ""
            assert _KNOWN_API_ENDPOINT.fullmatch(endpoint), f"unexpected gh api endpoint: {args}"
            flag_names = [_flag_name(a) for a in args]
            assert not (set(flag_names) & set(_WRITE_FIELD_FLAGS)), (
                f"gh api call carries a field flag, which flips the default method to POST: {args}"
            )
            for index, token in enumerate(args):
                if _flag_name(token) not in _WRITE_METHOD_FLAGS:
                    continue
                method_value = token.split("=", 1)[1] if "=" in token else args[index + 1]
                assert method_value.upper() == "GET", f"gh api call sets a non-GET method: {args}"
            continue
        pytest.fail(f"gh invocation outside the pr view/pr checks/api allowlist: {args}")


@pytest.fixture
def isolated_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    return home


def _gh_shim_source(
    call_log: Path,
    head_ref_oid: str | None = None,
    capped_files: list[str] | None = None,
    full_files: list[str] | None = None,
    capped_commits: list[str] | None = None,
    full_commits: list[str] | None = None,
    author_association: str = "MEMBER",
    reviews: list[dict] | None = None,
    checks: list[dict] | None = None,
    fail_pr_view: bool = False,
    fail_rest: bool = False,
    fail_files_paginate: bool = False,
    fail_commits_paginate: bool = False,
    fail_checks: bool = False,
    fail_reviews: bool = False,
    rest_error_text: str | None = None,
) -> str:
    """gh shim recording every invocation. `capped_files`/`capped_commits`
    model `gh pr view --json files/commits`' own 100-entry-capped arrays;
    `full_files`/`full_commits` (defaulting to the capped lists, i.e. no
    truncation) model the ground truth the REST `--paginate` re-fetch and
    the REST pulls payload's own `commits` integer respectively report --
    a caller passing a shorter capped list than the full one models the
    truncation this script must detect and repaginate past."""
    capped_files = capped_files if capped_files is not None else []
    full_files = full_files if full_files is not None else capped_files
    capped_commits = capped_commits if capped_commits is not None else []
    full_commits = full_commits if full_commits is not None else capped_commits
    reviews = reviews or []
    checks = checks or []
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json
        import sys

        CALL_LOG = {str(call_log)!r}
        HEAD_REF_OID = {head_ref_oid!r}
        CAPPED_FILES = {list(capped_files)!r}
        FULL_FILES = {list(full_files)!r}
        CAPPED_COMMITS = {list(capped_commits)!r}
        FULL_COMMITS = {list(full_commits)!r}
        AUTHOR_ASSOCIATION = {author_association!r}
        REVIEWS = {list(reviews)!r}
        CHECKS = {list(checks)!r}
        FAIL_PR_VIEW = {fail_pr_view!r}
        FAIL_REST = {fail_rest!r}
        FAIL_FILES_PAGINATE = {fail_files_paginate!r}
        FAIL_COMMITS_PAGINATE = {fail_commits_paginate!r}
        FAIL_CHECKS = {fail_checks!r}
        FAIL_REVIEWS = {fail_reviews!r}
        REST_ERROR_TEXT = {rest_error_text!r}
        args = sys.argv[1:]
        with open(CALL_LOG, "a") as f:
            f.write(json.dumps({{"args": args}}) + chr(10))

        if args[:2] == ["pr", "view"]:
            if FAIL_PR_VIEW:
                sys.exit(1)
            doc = {{
                "title": "t", "body": "b", "author": {{"login": "someone"}},
                "isCrossRepository": False, "baseRefOid": "0" * 40,
                "headRefOid": HEAD_REF_OID, "headRepositoryOwner": {{"login": "foo"}},
                "files": [{{"path": p, "additions": 1, "deletions": 0, "changeType": "MODIFIED"}} for p in CAPPED_FILES],
                "changedFiles": len(FULL_FILES),
                "commits": [{{"oid": c}} for c in CAPPED_COMMITS],
                "reviews": [],
                "reviewDecision": "", "mergeable": "UNKNOWN", "mergeStateStatus": "UNKNOWN",
            }}
            print(json.dumps(doc))
            sys.exit(0)
        if args[:2] == ["pr", "checks"]:
            if FAIL_CHECKS:
                sys.exit(1)
            print(json.dumps(CHECKS))
            sys.exit(0)
        if args[:1] == ["api"]:
            path = args[1] if len(args) > 1 else ""
            if path.endswith("/files"):
                if FAIL_FILES_PAGINATE:
                    sys.exit(1)
                for f in FULL_FILES:
                    print(f)
                sys.exit(0)
            if path.endswith("/commits"):
                if FAIL_COMMITS_PAGINATE:
                    sys.exit(1)
                for c in FULL_COMMITS:
                    print(c)
                sys.exit(0)
            if path.endswith("/reviews"):
                if FAIL_REVIEWS:
                    sys.exit(1)
                # Mirrors the real `--jq '.[] | select(.body != "")'`
                # filter the actual gh invocation applies server-side --
                # this shim stands in for gh entirely, so it must apply
                # the same selection rather than echoing every review back.
                for r in REVIEWS:
                    if r.get("body"):
                        selected = {{
                            "id": r.get("id"), "author": r.get("author"),
                            "state": r.get("state"), "body": r.get("body"),
                        }}
                        print(json.dumps(selected))
                sys.exit(0)
            if FAIL_REST:
                if REST_ERROR_TEXT:
                    print(REST_ERROR_TEXT, file=sys.stderr)
                sys.exit(1)
            print(json.dumps({{"author_association": AUTHOR_ASSOCIATION, "commits": len(FULL_COMMITS)}}))
            sys.exit(0)
        sys.exit(0)
    """)


def _read_calls(call_log: Path) -> list[list[str]]:
    if not call_log.exists():
        return []
    return [json.loads(line)["args"] for line in call_log.read_text().splitlines() if line]


def _run(
    home: Path,
    args: list[str],
    tmp_path: Path,
    **shim_kwargs,
) -> tuple[subprocess.CompletedProcess, Path]:
    _seed_session(home, SID)
    call_log = tmp_path / "gh_calls.jsonl"
    env = {**_shimmed_env(tmp_path, _gh_shim_source(call_log, **shim_kwargs)), "HOME": str(home)}
    env.pop("CLAUDE_CONFIG_DIR", None)
    result = subprocess.run(
        ["bash", str(SCRIPT), *args], cwd=tmp_path, env=env, capture_output=True, text=True,
    )
    _assert_gh_calls_are_read_only(_read_calls(call_log))
    return result, call_log


class TestUsageErrors:
    @pytest.mark.parametrize("args", [[], ["foo/bar#42", "extra"], ["no-hash-or-slash"], ["../..#5"]])
    def test_invalid_argv_exits_two_with_no_gh_call(self, isolated_home, tmp_path, args):
        result, call_log = _run(isolated_home, args, tmp_path)
        assert result.returncode == 2
        assert _read_calls(call_log) == []


class TestGhFailureAborts:
    def test_pr_view_failure_aborts(self, isolated_home, tmp_path):
        result, call_log = _run(isolated_home, [PR_IDENTITY], tmp_path, fail_pr_view=True)
        assert result.returncode != 0

    def test_author_association_rest_call_failure_aborts(self, isolated_home, tmp_path):
        result, call_log = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40, fail_rest=True,
        )
        assert result.returncode != 0

    def test_checks_failure_aborts(self, isolated_home, tmp_path):
        result, call_log = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40, fail_checks=True,
        )
        assert result.returncode != 0

    def test_reviews_failure_aborts(self, isolated_home, tmp_path):
        result, call_log = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40, fail_reviews=True,
        )
        assert result.returncode != 0


class TestSuccessfulAcquire:
    def test_prints_context_json_and_writes_backstop_and_provenance(self, isolated_home, tmp_path):
        result, call_log = _run(
            isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid="a" * 40, capped_files=["a.py"], capped_commits=["c1"],
        )
        assert result.returncode == 0, result.stderr

        stdout_doc = json.loads(result.stdout)
        assert stdout_doc["headRefOid"] == "a" * 40
        assert stdout_doc["authorAssociation"] == "MEMBER"
        assert stdout_doc["prIdentity"] == PR_IDENTITY

        context_file = isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.context.json"
        assert json.loads(context_file.read_text()) == stdout_doc

        provenance = isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.provenance"
        lines = provenance.read_text().splitlines()
        assert lines[0] == PR_IDENTITY
        assert lines[1] == "a" * 40
        assert lines[2].isdigit()
        assert lines[3] == "acquired"

    def test_reviews_with_empty_body_are_excluded(self, isolated_home, tmp_path):
        result, call_log = _run(
            isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid="a" * 40,
            reviews=[
                {"id": 1, "author": "x", "state": "COMMENTED", "body": ""},
                {"id": 2, "author": "y", "state": "APPROVED", "body": "looks good"},
            ],
        )
        assert result.returncode == 0, result.stderr
        doc = json.loads(result.stdout)
        assert len(doc["existingReviews"]) == 1
        assert doc["existingReviews"][0]["id"] == 2

    def test_checks_are_captured(self, isolated_home, tmp_path):
        result, call_log = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40,
            checks=[{"name": "ci", "state": "SUCCESS", "bucket": "pass"}],
        )
        assert result.returncode == 0, result.stderr
        doc = json.loads(result.stdout)
        assert doc["checks"] == [{"name": "ci", "state": "SUCCESS", "bucket": "pass"}]


class TestFilesReconciliation:
    def test_files_changed_files_mismatch_triggers_paginate_refetch(self, isolated_home, tmp_path):
        result, call_log = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40,
            capped_files=["a.py"], full_files=["a.py", "b.py", "c.py"],
        )
        assert result.returncode == 0, result.stderr
        doc = json.loads(result.stdout)
        assert sorted(doc["files"]) == ["a.py", "b.py", "c.py"]
        calls = _read_calls(call_log)
        files_calls = [c for c in calls if c[:1] == ["api"] and c[1].endswith("/files")]
        assert files_calls, "a length mismatch must trigger the --paginate re-fetch"
        assert "--paginate" in files_calls[0]

    def test_no_mismatch_skips_the_paginate_refetch(self, isolated_home, tmp_path):
        result, call_log = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40,
            capped_files=["a.py"], full_files=["a.py"],
        )
        assert result.returncode == 0, result.stderr
        calls = _read_calls(call_log)
        assert not [c for c in calls if c[:1] == ["api"] and c[1].endswith("/files")]

    def test_paginate_refetch_failure_aborts_rather_than_trusting_the_capped_list(
        self, isolated_home, tmp_path
    ):
        result, call_log = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40,
            capped_files=["a.py"], full_files=["a.py", "b.py"], fail_files_paginate=True,
        )
        assert result.returncode != 0


class TestCommitsReconciliation:
    def test_commits_count_mismatch_triggers_paginate_refetch(self, isolated_home, tmp_path):
        """The REST payload's own `.commits` integer (the true total) is
        the reconciliation source -- distinct from `gh pr view --json
        commits`' own capped array."""
        result, call_log = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40,
            capped_commits=["c1"], full_commits=["c1", "c2", "c3"],
        )
        assert result.returncode == 0, result.stderr
        doc = json.loads(result.stdout)
        assert sorted(doc["commits"]) == ["c1", "c2", "c3"]
        calls = _read_calls(call_log)
        commits_calls = [c for c in calls if c[:1] == ["api"] and c[1].endswith("/commits")]
        assert commits_calls
        assert "--paginate" in commits_calls[0]

    def test_no_mismatch_skips_the_paginate_refetch(self, isolated_home, tmp_path):
        result, call_log = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40,
            capped_commits=["c1"], full_commits=["c1"],
        )
        assert result.returncode == 0, result.stderr
        calls = _read_calls(call_log)
        assert not [c for c in calls if c[:1] == ["api"] and c[1].endswith("/commits")]


class TestGhFailureNeverBypassesTheScrub:
    """A `gh` failure's stderr/error output must never be captured verbatim
    into `.context.json` -- GitHub API error payloads occasionally echo
    request parameters."""

    def test_rest_call_failure_error_text_never_reaches_context_json_or_output(
        self, isolated_home, tmp_path
    ):
        secret_shaped_text = "leaked-token=ghp_AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHHIIII"
        result, call_log = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40,
            fail_rest=True, rest_error_text=secret_shaped_text,
        )
        assert result.returncode != 0
        assert secret_shaped_text not in result.stdout
        assert secret_shaped_text not in result.stderr
        context_file = isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.context.json"
        assert not context_file.exists()
