"""Tests for review-pr-acquire.sh -- /review-pr's acquire step, one script
that owns its own pagination reconciliation for the 100-entry caps
`gh pr view --json` carries on `files` and `commits`.

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

from .conftest import (
    GH_CONTROL_CHARACTER_SANITIZER_SHIM_LINE,
    _provenance_fields,
    _seed_session,
    _shimmed_env,
)

SCRIPT = SCRIPTS_DIR / "review-pr-acquire.sh"
OWNER_REPO = "foo/bar"
PR_NUMBER = "42"
PR_IDENTITY = f"{OWNER_REPO}#{PR_NUMBER}"
SID = "test-session-review-pr-acquire"

# A harness bound so a hung bash fails one test instead of the suite.
_SUBPROCESS_TIMEOUT_SECONDS = 60

# gh api's own default-method rule (`gh api --help`): GET unless a field flag
# is present, in which case the default flips to POST.
_WRITE_METHOD_FLAGS = ("-X", "--method")
_WRITE_FIELD_FLAGS = ("-f", "-F", "--field", "--raw-field")
_KNOWN_API_ENDPOINT = re.compile(
    rf"repos/{re.escape(OWNER_REPO)}/pulls/{re.escape(PR_NUMBER)}(/(files|commits|reviews|comments)\?per_page=100)?$"
)


def _is_listing_call(call: list[str], suffix: str) -> bool:
    """True for a `gh api` call to the pulls listing ending in `suffix`,
    whatever query string it carries."""
    return call[:1] == ["api"] and call[1].split("?", 1)[0].endswith(suffix)


def _flag_name(token: str) -> str:
    """"--method=GET" and "--method" "GET" are both valid gh/cobra flag
    syntax -- strip a glued "=value" suffix so a flag-name comparison
    catches either form."""
    return token.split("=", 1)[0]


def _assert_gh_calls_are_read_only(calls: list[list[str]]) -> None:
    """Allowlists the exact `gh` call shapes review-pr-acquire.sh makes (its
    usage text lists the fetches): `gh pr view` and `gh api` against its
    own known GET endpoints (the bare pulls/{N} resource plus its
    files/commits/reviews/comments sub-resources, each at the maximum page
    size), none carrying a method or field flag that would flip `gh api`'s
    default method to POST. Any call outside that allowlist fails, including
    an unanticipated write shape this suite's fixtures never modeled.

    Covers only the `gh` invocations the shimmed code paths this suite's
    fixtures drive actually make; it says nothing about a non-`gh` write
    (e.g. a raw `curl`) review-pr-acquire.sh might issue.
    """
    for args in calls:
        if args[:2] == ["pr", "view"]:
            assert "-R" in args and "--json" in args, f"gh pr view missing expected flags: {args}"
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
        pytest.fail(f"gh invocation outside the pr view/api allowlist: {args}")


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
    author_association: str | None = "MEMBER",
    reviews: list[dict] | None = None,
    inline_comments: list[dict] | None = None,
    status_check_rollup: list[dict] | None = None,
    null_status_check_rollup: bool = False,
    rest_changed_files: int | str | None = None,
    omit_rest_changed_files: bool = False,
    rest_commits_total: int | None = None,
    omit_rest_commits_total: bool = False,
    fail_pr_view: bool = False,
    fail_rest: bool = False,
    fail_files_paginate: bool = False,
    listing_failure_exit_status: int = 1,
    fail_commits_paginate: bool = False,
    fail_reviews: bool = False,
    fail_inline_comments: bool = False,
    rest_error_text: str | None = None,
) -> str:
    """gh shim recording every invocation in `call_log`.
    `capped_files`/`capped_commits` model `gh pr view --json files/commits`'
    own 100-entry-capped arrays. `full_files`/`full_commits` (default: the
    capped lists, i.e. no truncation) model the ground truth the `--paginate`
    re-fetch and the REST `commits` integer report, so a shorter capped list
    models the truncation the script must detect.
    `rest_changed_files`/`rest_commits_total` override the REST integers
    (default: the length of the full list), so a test can model a re-fetch that
    still falls short of the PR's real total.
    `gh pr view` returns only the fields named after `--json` and exits 1 on
    an unknown field, as the real binary does. The files listing prints one
    JSON string per line under the `@json` jq filter, with a control character
    rendered in caret notation as gh 2.100.0 is modeled to do (see the shim
    constant in conftest.py).
    Every listing call must carry `--paginate` and `per_page=100`; the shim
    exits 97 otherwise."""
    capped_files = capped_files if capped_files is not None else []
    full_files = full_files if full_files is not None else capped_files
    capped_commits = capped_commits if capped_commits is not None else []
    full_commits = full_commits if full_commits is not None else capped_commits
    reviews = reviews or []
    inline_comments = inline_comments or []
    status_check_rollup = status_check_rollup if status_check_rollup is not None else []
    rest_changed_files = len(full_files) if rest_changed_files is None else rest_changed_files
    rest_commits_total = len(full_commits) if rest_commits_total is None else rest_commits_total
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
        INLINE_COMMENTS = {list(inline_comments)!r}
        STATUS_CHECK_ROLLUP = {None if null_status_check_rollup else list(status_check_rollup)!r}
        REST_CHANGED_FILES = {rest_changed_files!r}
        OMIT_REST_CHANGED_FILES = {omit_rest_changed_files!r}
        REST_COMMITS_TOTAL = {rest_commits_total!r}
        OMIT_REST_COMMITS_TOTAL = {omit_rest_commits_total!r}
        FAIL_PR_VIEW = {fail_pr_view!r}
        FAIL_REST = {fail_rest!r}
        FAIL_FILES_PAGINATE = {fail_files_paginate!r}
        LISTING_FAILURE_EXIT_STATUS = {listing_failure_exit_status!r}
        FAIL_COMMITS_PAGINATE = {fail_commits_paginate!r}
        FAIL_REVIEWS = {fail_reviews!r}
        FAIL_INLINE_COMMENTS = {fail_inline_comments!r}
        REST_ERROR_TEXT = {rest_error_text!r}
        {GH_CONTROL_CHARACTER_SANITIZER_SHIM_LINE}
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
                "statusCheckRollup": STATUS_CHECK_ROLLUP,
            }}
            requested = args[args.index("--json") + 1].split(",")
            unknown = [name for name in requested if name not in doc]
            if unknown:
                print("Unknown JSON field: " + ", ".join(unknown), file=sys.stderr)
                sys.exit(1)
            print(json.dumps({{name: doc[name] for name in requested}}))
            sys.exit(0)
        if args[:1] == ["api"]:
            path, _, query = (args[1] if len(args) > 1 else "").partition("?")
            if path.endswith(("/files", "/commits", "/reviews", "/comments")):
                if "--paginate" not in args or query != "per_page=100":
                    print("shim: listing call without --paginate and per_page=100", file=sys.stderr)
                    sys.exit(97)
            if path.endswith("/files"):
                if FAIL_FILES_PAGINATE:
                    sys.exit(LISTING_FAILURE_EXIT_STATUS)
                encode = (
                    (lambda name: json.dumps(name, ensure_ascii=False))
                    if ".[].filename | @json" in args else str
                )
                for f in FULL_FILES:
                    sys.stdout.buffer.write((encode(sanitize_like_gh(f)) + chr(10)).encode("utf-8"))
                sys.exit(0)
            if path.endswith("/commits"):
                if FAIL_COMMITS_PAGINATE:
                    sys.exit(LISTING_FAILURE_EXIT_STATUS)
                for c in FULL_COMMITS:
                    print(c)
                sys.exit(0)
            if path.endswith("/reviews"):
                if FAIL_REVIEWS:
                    sys.exit(LISTING_FAILURE_EXIT_STATUS)
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
            if path.endswith("/comments"):
                if FAIL_INLINE_COMMENTS:
                    sys.exit(LISTING_FAILURE_EXIT_STATUS)
                # Mirrors the real `--jq '.[] | {{author, path, line, body}}'`
                # projection, like the reviews branch above.
                for c in INLINE_COMMENTS:
                    print(json.dumps({{
                        "author": c.get("author"), "path": c.get("path"),
                        "line": c.get("line"), "body": c.get("body"),
                    }}))
                sys.exit(0)
            if FAIL_REST:
                if REST_ERROR_TEXT:
                    print(REST_ERROR_TEXT, file=sys.stderr)
                sys.exit(1)
            rest_payload = {{"author_association": AUTHOR_ASSOCIATION}}
            if not OMIT_REST_COMMITS_TOTAL:
                rest_payload["commits"] = REST_COMMITS_TOTAL
            if not OMIT_REST_CHANGED_FILES:
                rest_payload["changed_files"] = REST_CHANGED_FILES
            print(json.dumps(rest_payload))
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
    extra_env: dict | None = None,
    **shim_kwargs,
) -> tuple[subprocess.CompletedProcess, Path]:
    _seed_session(home, SID)
    call_log = tmp_path / "gh_calls.jsonl"
    env = {**_shimmed_env(tmp_path, _gh_shim_source(call_log, **shim_kwargs)), "HOME": str(home)}
    env.pop("CLAUDE_CONFIG_DIR", None)
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        ["bash", str(SCRIPT), *args], cwd=tmp_path, env=env, capture_output=True, text=True,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    )
    _assert_gh_calls_are_read_only(_read_calls(call_log))
    return result, call_log


def _assert_no_partial_document(result: subprocess.CompletedProcess, home: Path) -> None:
    """An abort prints no document on stdout and leaves no backstop file."""
    assert result.stdout == ""
    assert not (home / ".claude" / ".review-pr-active.d" / f"{SID}.context.json").exists()


class TestUsageErrors:
    @pytest.mark.parametrize("args", [[], ["foo/bar#42", "extra"], ["no-hash-or-slash"], ["../..#5"]])
    def test_invalid_argv_exits_two_with_no_gh_call(self, isolated_home, tmp_path, args):
        result, call_log = _run(isolated_home, args, tmp_path)
        assert result.returncode == 2
        assert _read_calls(call_log) == []


class TestActiveDirectoryCreationFailure:
    def test_a_file_where_the_active_directory_belongs_aborts_with_exit_two_before_any_gh_call(
        self, isolated_home, tmp_path
    ):
        """`mkdir -p` cannot create a directory over a regular file. Under
        `set -e` an unguarded call would exit with mkdir's own status 1."""
        active_path = isolated_home / ".claude" / ".review-pr-active.d"
        active_path.parent.mkdir(exist_ok=True)
        active_path.write_text("not a directory\n")
        result, call_log = _run(isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40)
        assert result.returncode == 2
        assert "could not create the active directory" in result.stderr
        assert result.stdout == ""
        assert _read_calls(call_log) == []
        assert active_path.read_text() == "not a directory\n", "pre-existing file is left untouched"


class TestGhFailureAborts:
    def test_pr_view_failure_aborts(self, isolated_home, tmp_path):
        result, call_log = _run(isolated_home, [PR_IDENTITY], tmp_path, fail_pr_view=True)
        assert result.returncode == 2
        _assert_no_partial_document(result, isolated_home)

    def test_author_association_rest_call_failure_aborts(self, isolated_home, tmp_path):
        result, call_log = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40, fail_rest=True,
        )
        assert result.returncode == 2
        _assert_no_partial_document(result, isolated_home)

    @pytest.mark.parametrize("author_association", [None, ""], ids=["null", "empty"])
    def test_missing_author_association_aborts(self, isolated_home, tmp_path, author_association):
        result, _ = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40,
            author_association=author_association,
        )
        assert result.returncode == 2
        assert "author_association" in result.stderr
        assert result.stdout == ""

    def test_reviews_failure_aborts(self, isolated_home, tmp_path):
        result, call_log = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40, fail_reviews=True,
        )
        assert result.returncode == 2
        _assert_no_partial_document(result, isolated_home)

    def test_inline_comments_failure_aborts(self, isolated_home, tmp_path):
        result, call_log = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40, fail_inline_comments=True,
        )
        assert result.returncode == 2
        _assert_no_partial_document(result, isolated_home)

    @pytest.mark.parametrize(
        "shim_failure_option,fetch_description",
        [
            ("fail_files_paginate", "files/changedFiles counts disagree and the full re-fetch (gh api --paginate)"),
            ("fail_commits_paginate", "commit counts disagree and the full re-fetch (gh api --paginate)"),
            ("fail_reviews", "existing reviews (gh api --paginate"),
            ("fail_inline_comments", "existing inline comments (gh api --paginate"),
        ],
        ids=["files", "commits", "reviews", "inline-comments"],
    )
    @pytest.mark.parametrize(
        "exit_status,status_wording",
        [(1, "failed (exit 1)"), (124, "timed out")],
        ids=["gh-failed", "cap-kill-124"],
    )
    def test_a_failed_paginated_fetch_names_a_timeout_apart_from_any_other_failure(
        self, isolated_home, tmp_path, shim_failure_option, fetch_description, exit_status, status_wording
    ):
        result, _ = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40,
            capped_files=["a.py"], full_files=["a.py", "b.py"],
            capped_commits=["c1"], full_commits=["c1", "c2"],
            listing_failure_exit_status=exit_status, **{shim_failure_option: True},
        )
        assert result.returncode == 2
        assert fetch_description in result.stderr
        assert status_wording in result.stderr
        _assert_no_partial_document(result, isolated_home)


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
        assert stdout_doc["filesComplete"] is True
        assert stdout_doc["commitsComplete"] is True

        context_file = isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.context.json"
        assert json.loads(context_file.read_text()) == stdout_doc

        provenance = isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.provenance"
        fields = _provenance_fields(provenance)
        assert fields["pr_identity"] == PR_IDENTITY
        assert fields["head_ref_oid"] == "a" * 40
        assert fields["pid"].isdigit()
        assert fields["mode"] == "acquired"

    def test_completeness_flags_are_the_first_keys_of_the_document(self, isolated_home, tmp_path):
        """The completeness flags SKILL.md tells the model to check come
        first, so a cut at the end of a large document keeps them."""
        result, call_log = _run(
            isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid="a" * 40, capped_files=["a.py"], capped_commits=["c1"],
        )
        assert result.returncode == 0, result.stderr
        assert list(json.loads(result.stdout))[:2] == ["filesComplete", "commitsComplete"]

    def test_last_stderr_line_names_the_context_backstop_file(self, isolated_home, tmp_path):
        result, call_log = _run(
            isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid="a" * 40, capped_files=["a.py"], capped_commits=["c1"],
        )
        assert result.returncode == 0, result.stderr
        context_file = isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.context.json"
        assert result.stderr.splitlines()[-1].endswith(f": {context_file}")

    def test_backstop_file_is_multi_line_while_stdout_is_one_compact_line(self, isolated_home, tmp_path):
        """The Read tool pages a file by line, so a single-line backstop file
        could not be read in slices; stdout stays one compact document."""
        result, call_log = _run(
            isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid="a" * 40, capped_files=["a.py"], capped_commits=["c1"],
        )
        assert result.returncode == 0, result.stderr
        context_file = isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.context.json"
        file_text = context_file.read_text()
        assert len(file_text.splitlines()) > 1
        assert json.loads(file_text) == json.loads(result.stdout)
        assert len(result.stdout.splitlines()) == 1

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

    def test_reviews_key_is_not_requested_so_review_bodies_appear_once(self, isolated_home, tmp_path):
        """`existingReviews` (the paginated fetch) is the only source of review
        bodies; also requesting `reviews` from `gh pr view` would emit every
        body a second time under a second key."""
        result, call_log = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40,
            reviews=[{"id": 2, "author": "y", "state": "APPROVED", "body": "looks good"}],
        )
        assert result.returncode == 0, result.stderr
        doc = json.loads(result.stdout)
        assert "reviews" not in doc
        assert len(doc["existingReviews"]) == 1
        pr_view_calls = [c for c in _read_calls(call_log) if c[:2] == ["pr", "view"]]
        assert "reviews" not in pr_view_calls[0][pr_view_calls[0].index("--json") + 1].split(",")


class TestExistingInlineComments:
    def test_inline_comment_from_a_reviewer_with_no_review_body_is_captured(
        self, isolated_home, tmp_path
    ):
        """A prior reviewer who left only inline comments has an empty review
        body, so the reviews fetch drops them; the comments fetch is what
        lets a later dedup pass see what they already raised."""
        result, call_log = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40,
            reviews=[{"id": 7, "author": "inline-only", "state": "COMMENTED", "body": ""}],
            inline_comments=[
                {"author": "inline-only", "path": "src/app.py", "line": 12, "body": "off by one"},
                {"author": "inline-only", "path": "src/app.py", "line": None, "body": "outdated note"},
            ],
        )
        assert result.returncode == 0, result.stderr
        doc = json.loads(result.stdout)
        assert doc["existingReviews"] == []
        assert doc["existingInlineComments"] == [
            {"author": "inline-only", "path": "src/app.py", "line": 12, "body": "off by one"},
            {"author": "inline-only", "path": "src/app.py", "line": None, "body": "outdated note"},
        ]
        comments_calls = [c for c in _read_calls(call_log) if _is_listing_call(c, "/comments")]
        assert comments_calls and "--paginate" in comments_calls[0]

    def test_no_inline_comments_yields_an_empty_list(self, isolated_home, tmp_path):
        result, _ = _run(isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["existingInlineComments"] == []


class TestStatusCheckRollupPassThrough:
    """Check results come from `statusCheckRollup`. Its entries are passed
    through raw -- nothing downstream branches on them, so the CheckRun and
    StatusContext shapes are not normalized -- and a null value reads as an
    empty list."""

    def test_status_check_rollup_is_requested_in_the_pr_view_fields(self, isolated_home, tmp_path):
        result, call_log = _run(isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40)
        assert result.returncode == 0, result.stderr
        calls = _read_calls(call_log)
        assert not [c for c in calls if c[:2] == ["pr", "checks"]]
        pr_view_calls = [c for c in calls if c[:2] == ["pr", "view"]]
        assert "statusCheckRollup" in pr_view_calls[0][pr_view_calls[0].index("--json") + 1].split(",")
        assert "checks" not in json.loads(result.stdout)

    def test_null_status_check_rollup_from_gh_is_normalized_to_an_empty_list(self, isolated_home, tmp_path):
        result, _ = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40, null_status_check_rollup=True,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["statusCheckRollup"] == []

    def test_check_run_and_status_context_entries_pass_through_unchanged(self, isolated_home, tmp_path):
        rollup = [
            {"__typename": "CheckRun", "name": "ci", "status": "COMPLETED", "conclusion": "SUCCESS"},
            {"__typename": "StatusContext", "context": "legacy-ci", "state": "PENDING"},
        ]
        result, _ = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40, status_check_rollup=rollup,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["statusCheckRollup"] == rollup


class TestContextAssemblyOfOversizedLists:
    """A single argv string is capped at 128 KiB on Linux, so lists handed to
    jq as `--argjson` values fail once a large PR's review bodies or file
    list pass that size. They travel through files instead, in a temp
    directory the script removes on exit."""

    def test_review_bodies_past_the_single_argument_limit_assemble_and_leave_no_temp_dir(
        self, isolated_home, tmp_path
    ):
        oversized_body = "x" * 300_000
        scratch_root = tmp_path / "scratch"
        scratch_root.mkdir()
        result, _ = _run(
            isolated_home, [PR_IDENTITY], tmp_path, extra_env={"TMPDIR": str(scratch_root)},
            head_ref_oid="a" * 40,
            reviews=[{"id": 1, "author": "y", "state": "COMMENTED", "body": oversized_body}],
        )
        assert result.returncode == 0, result.stderr
        doc = json.loads(result.stdout)
        assert doc["existingReviews"][0]["body"] == oversized_body
        assert list(scratch_root.iterdir()) == [], "the context-assembly temp directory must be removed on exit"


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
        files_calls = [c for c in calls if _is_listing_call(c, "/files")]
        assert files_calls, "a length mismatch must trigger the --paginate re-fetch"
        assert "--paginate" in files_calls[0]

    def test_no_mismatch_skips_the_paginate_refetch(self, isolated_home, tmp_path):
        result, call_log = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40,
            capped_files=["a.py"], full_files=["a.py"],
        )
        assert result.returncode == 0, result.stderr
        calls = _read_calls(call_log)
        assert not [c for c in calls if _is_listing_call(c, "/files")]

    @pytest.mark.parametrize(
        "exit_status,status_wording",
        [(1, "failed (exit 1)"), (124, "timed out"), (137, "failed (exit 137)")],
        ids=["gh-failed", "cap-kill-124", "sigkill-137"],
    )
    def test_paginate_refetch_failure_aborts_rather_than_trusting_the_capped_list(
        self, isolated_home, tmp_path, exit_status, status_wording
    ):
        result, call_log = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40,
            capped_files=["a.py"], full_files=["a.py", "b.py"],
            fail_files_paginate=True, listing_failure_exit_status=exit_status,
        )
        assert result.returncode == 2
        assert f"(gh api --paginate) {status_wording}" in result.stderr
        assert result.stdout == ""

    def test_refetched_list_that_reconciles_reports_files_complete(self, isolated_home, tmp_path):
        result, _ = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40,
            capped_files=["a.py"], full_files=["a.py", "b.py"],
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["filesComplete"] is True

    def test_refetched_names_decode_intact_and_none_is_refused_for_its_content(
        self, isolated_home, tmp_path
    ):
        """The re-fetch decodes one JSON string per name: a name holding a raw
        newline stays one name, a non-ASCII, quote or space name keeps its
        bytes, and a caret (gh's modeled rendering of a control character) is kept.
        Acquire has no line-splitting consumer, so no name is refused."""
        names = ["a.py", "docs/notes.txt\nevil.sh", 'docs/caf\u00e9 "notes".md', "src/a^[b.py"]
        result, call_log = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40,
            capped_files=["a.py"], full_files=names,
        )
        assert result.returncode == 0, result.stderr
        doc = json.loads(result.stdout)
        assert doc["files"] == names
        assert doc["filesComplete"] is True
        files_calls = [c for c in _read_calls(call_log) if _is_listing_call(c, "/files")]
        assert ".[].filename | @json" in files_calls[0]

    def test_refetched_list_shorter_than_the_rest_changed_files_aborts_with_a_truncation_message(
        self, isolated_home, tmp_path
    ):
        """The REST files listing stops short of a very large PR's real total
        without an error; the re-fetch agreeing with `gh pr view`'s own
        `changedFiles` does not make it complete."""
        result, _ = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40,
            capped_files=["a.py"], full_files=["a.py", "b.py"], rest_changed_files=3001,
        )
        assert result.returncode == 2
        assert "truncated, or the PR changed while it was being fetched" in result.stderr
        assert "Retry" in result.stderr
        _assert_no_partial_document(result, isolated_home)

    def test_list_from_gh_pr_view_that_disagrees_with_the_rest_total_reports_files_incomplete(
        self, isolated_home, tmp_path
    ):
        """No re-fetch runs (gh pr view's own counts agree), yet the REST
        total differs -- the PR moved between the two calls. The document
        still prints, flagged incomplete."""
        result, call_log = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40,
            capped_files=["a.py"], full_files=["a.py"], rest_changed_files=2,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["filesComplete"] is False
        assert not [c for c in _read_calls(call_log) if _is_listing_call(c, "/files")]

    def test_missing_rest_changed_files_aborts(self, isolated_home, tmp_path):
        result, _ = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40,
            capped_files=["a.py"], omit_rest_changed_files=True,
        )
        assert result.returncode == 2
        assert "changed_files" in result.stderr
        assert result.stdout == ""


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
        commits_calls = [c for c in calls if _is_listing_call(c, "/commits")]
        assert commits_calls
        assert "--paginate" in commits_calls[0]

    def test_no_mismatch_skips_the_paginate_refetch(self, isolated_home, tmp_path):
        result, call_log = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40,
            capped_commits=["c1"], full_commits=["c1"],
        )
        assert result.returncode == 0, result.stderr
        calls = _read_calls(call_log)
        assert not [c for c in calls if _is_listing_call(c, "/commits")]

    def test_exactly_one_hundred_commits_needs_no_refetch_and_is_complete(self, isolated_home, tmp_path):
        """`gh pr view --json commits` returns at most 100 entries, so a PR
        with exactly 100 commits fills the array and still matches the REST
        total."""
        hundred_commits = [f"c{n}" for n in range(100)]
        result, call_log = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40,
            capped_commits=hundred_commits, full_commits=hundred_commits,
        )
        assert result.returncode == 0, result.stderr
        doc = json.loads(result.stdout)
        assert doc["commitsComplete"] is True
        assert not [c for c in _read_calls(call_log) if _is_listing_call(c, "/commits")]

    def test_commit_count_past_the_gh_pr_view_cap_is_refetched_and_complete(self, isolated_home, tmp_path):
        all_commits = [f"c{n}" for n in range(104)]
        result, call_log = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40,
            capped_commits=all_commits[:100], full_commits=all_commits,
        )
        assert result.returncode == 0, result.stderr
        doc = json.loads(result.stdout)
        assert doc["commits"] == all_commits
        assert doc["commitsComplete"] is True
        assert [c for c in _read_calls(call_log) if _is_listing_call(c, "/commits")]

    def test_absent_rest_commits_total_skips_the_refetch_and_flags_commits_incomplete(
        self, isolated_home, tmp_path
    ):
        """No true total to reconcile against, so the list cannot be vouched
        for: the run still succeeds, flagged incomplete, without a re-fetch."""
        result, call_log = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40,
            capped_commits=["c1"], full_commits=["c1"], omit_rest_commits_total=True,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["commitsComplete"] is False
        assert not [c for c in _read_calls(call_log) if _is_listing_call(c, "/commits")]

    def test_refetched_commit_list_still_short_of_the_rest_total_is_flagged_incomplete(
        self, isolated_home, tmp_path
    ):
        """The re-fetch itself can fall short on a very long PR, so
        completeness comes from the final count against the REST total, not
        from the re-fetch having run."""
        result, _ = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40,
            capped_commits=["c1"], full_commits=["c1", "c2"], rest_commits_total=300,
        )
        assert result.returncode == 0, result.stderr
        doc = json.loads(result.stdout)
        assert doc["commits"] == ["c1", "c2"]
        assert doc["commitsComplete"] is False


class TestEveryListingPaginatesAtTheMaximumPageSize:
    def test_files_commits_reviews_and_comments_listings_carry_paginate_and_per_page(
        self, isolated_home, tmp_path
    ):
        result, call_log = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40,
            capped_files=["a.py"], full_files=["a.py", "b.py"],
            capped_commits=["c1"], full_commits=["c1", "c2"],
            reviews=[{"id": 1, "author": "y", "state": "COMMENTED", "body": "note"}],
            inline_comments=[{"author": "y", "path": "a.py", "line": 1, "body": "note"}],
        )
        assert result.returncode == 0, result.stderr
        listing_calls = [c for c in _read_calls(call_log) if c[:1] == ["api"] and "?" in c[1]]
        assert sorted(c[1].split("?")[0].rsplit("/", 1)[1] for c in listing_calls) == [
            "comments", "commits", "files", "reviews",
        ]
        for call in listing_calls:
            assert "--paginate" in call
            assert call[1].endswith("?per_page=100")


class TestGhFailureNeverBypassesTheScrub:
    """A `gh` failure's stderr/error output must never be captured verbatim
    into `.context.json` -- GitHub API error payloads occasionally echo
    request parameters."""

    def test_rest_call_failure_error_text_never_reaches_context_json_or_output(
        self, isolated_home, tmp_path
    ):
        secret_shaped_text = "leaked-token=ghp_" + "AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHHIIII"
        result, call_log = _run(
            isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid="a" * 40,
            fail_rest=True, rest_error_text=secret_shaped_text,
        )
        assert result.returncode == 2
        assert secret_shaped_text not in result.stdout
        assert secret_shaped_text not in result.stderr
        context_file = isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.context.json"
        assert not context_file.exists()
