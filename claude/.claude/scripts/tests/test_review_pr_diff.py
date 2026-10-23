"""Tests for review-pr-diff.sh -- the no-checkout review path for a
restricted (cross-repo or first-time-contributor) PR that
review-pr-checkout.sh's trust block refuses. Mirrors
review-pr-checkout.sh's self-derivation discipline (own PR-identity parse,
own origin-identity check, own paginated file list, own double headRefOid
fetch) minus everything that only matters once a PR is checked out.

The `gh` CLI is replaced by a PATH shim that records every invocation it
receives, matching test_review_pr_checkout.py's own shim pattern -- git is
left real (not shimmed).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
from helpers import SCRIPTS_DIR, write_review_pr_provenance

from ._review_pr_stack_helpers import (
    STACK_LOWER_PR_NUMBER,
    STACK_UPPER_PR_NUMBER,
    copy_script_with_stubbed_walk,
    pr_list_calls,
    pr_list_head_values,
    pr_list_item,
    stack_replies,
)
from .conftest import (
    _BASE_FROM_ORIGIN_MAIN,
    GH_CONTROL_CHARACTER_SANITIZER_SHIM_LINE,
    _assert_finish_and_clear_stale_each_empty_the_active_directory,
    _assert_gh_calls_are_read_only,
    _build_repo_with_pr_ref,
    _fetch_argument_lines,
    _fetch_askpass_values,
    _fetch_terminal_prompt_values,
    _git_commit_is_present,
    _git_shim_that_fails_on_subcommand,
    _git_shim_that_logs_terminal_prompt_on_fetch,
    _install_audit_script,
    _install_audit_script_that_runs,
    _install_reference_transaction_sentinel_hooks,
    _origin_main_sha,
    _provenance_fields,
    _push_new_base_commit,
    _pythonpath_env_that_forges_a_clean_audit,
    _review_pr_lib_integer,
    _seed_session,
    _shim_match_count,
    _shimmed_env,
    _skip_unless_a_hooks_enabled_fetch_by_object_name_fires_reference_transaction,
)

SCRIPT = SCRIPTS_DIR / "review-pr-diff.sh"
OWNER_REPO = "foo/bar"
PR_NUMBER = "42"
PR_IDENTITY = f"{OWNER_REPO}#{PR_NUMBER}"
SID = "test-session-review-pr-diff"

# A harness bound so a hung bash or interpreter fails one test instead of the suite.
_SUBPROCESS_TIMEOUT_SECONDS = 60

# The only `gh api` endpoints the script reads: the PR resource and its file listing.
_KNOWN_API_ENDPOINT = re.compile(r"repos/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+/pulls/[0-9]+(/files\?per_page=100)?")


@pytest.fixture
def isolated_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    return home


@pytest.fixture
def repo_with_pr_ref(tmp_path):
    return _build_repo_with_pr_ref(tmp_path, owner_repo=OWNER_REPO, pr_number=PR_NUMBER)


def _is_listing_call(call: list[str], suffix: str) -> bool:
    """True for a `gh api` call to the pulls listing ending in `suffix`,
    whatever query string it carries."""
    return call[:1] == ["api"] and call[1].split("?", 1)[0].endswith(suffix)


def _diff_text_for(files: list[str]) -> str:
    """A minimal but structurally valid unified diff: one `diff --git`
    header per file."""
    parts = []
    for f in files:
        parts.append(f"diff --git a/{f} b/{f}\nindex 000..111 100644\n--- a/{f}\n+++ b/{f}\n@@ -0,0 +1 @@\n+x\n")
    return "".join(parts)


# Sentinel default for pr_view_base_ref_name: "the base branch the REST payload reports".
_SAME_AS_REST_BASE_REF = "__SAME_AS_REST_BASE_REF__"

# Passed as a base-name knob to make the shim print JSON null for that field, where None drops the field.
_JSON_NULL = "__JSON_NULL__"


def _gh_shim_source(
    call_log: Path,
    *,
    head_ref_oid: str | None = None,
    files: list[str] | None = None,
    diff_text: str | None = None,
    fail_pr_view: bool = False,
    fail_files: bool = False,
    head_ref_oid_second: str | None = None,
    fail_diff: bool = False,
    diff_error_text: str | None = None,
    changed_files: int | str | None = None,
    omit_changed_files: bool = False,
    fail_rest: bool = False,
    files_failure_exit_status: int = 1,
    head_ref_oid_third: str | None = None,
    base_ref_oid: str | None = None,
    base_ref_name: str | None = "main",
    base_default_branch: str | None = "main",
    pr_view_base_ref_name: str | None = _SAME_AS_REST_BASE_REF,
    pr_list_replies: dict | None = None,
) -> str:
    """gh shim recording every invocation and returning fixed replies whatever
    fields are requested. A `gh pr view` call carrying
    `--jq` prints the headRefOid alone, as the script's re-fetches expect; one
    without it prints the JSON reply holding headRefOid and, unless
    `base_ref_oid` is None, baseRefOid, and, unless the `baseRefName` it
    reports is None, baseRefName. `diff_text` defaults to a
    structurally valid diff matching `files` (one `diff --git` header per
    file) unless explicitly overridden. `fail_diff` exits 1 on the `gh pr
    diff` call, printing `diff_error_text` to stderr if given -- models a
    `gh` failure whose stderr could echo request parameters, which the
    script must never capture into the `.diff` file or its own output.
    `gh api .../pulls/N` (no trailing `/files`) returns the REST payload's
    own `changed_files`, which defaults to `len(files)` and is set apart
    from `files` to model a truncated or padded listing (`omit_changed_files`
    drops it, `fail_rest` exits 1). `base_ref_name` and `base_default_branch`
    are the REST payload's `base.ref` and `base.repo.default_branch`; a value
    of None drops that field and `_JSON_NULL` makes it JSON null, independently of `omit_changed_files`.
    `pr_view_base_ref_name` is the `baseRefName` the non-`--jq` `gh pr view`
    reply reports (the REST `base.ref` by default, None drops it, `_JSON_NULL` makes it JSON null).
    `gh pr list` is answered by the stack-walk double (_review_pr_stack_helpers.py)
    from `pr_list_replies`, keyed by the exact `--head=` value; a branch with no
    key answers `[]`, which leaves a stacked base clear. The files
    listing prints one JSON string per line under the `@json` jq filter, as
    real `gh --jq` does for a
    string result: raw UTF-8, with a control character rendered in caret
    notation, as gh 2.100.0 is modeled to do (see the shim constant in
    conftest.py). The listing call must carry `--paginate` and
    `per_page=100`; the shim exits 97 otherwise, so a script dropping either
    fails the run. `fail_files` exits
    `files_failure_exit_status` on the listing call. `head_ref_oid_third`
    replaces the headRefOid from the THIRD `gh pr view` call onward, modeling a
    force-push during the diff fetch."""
    resolved_diff_text = diff_text if diff_text is not None else _diff_text_for(files or [])
    view_base_ref_name = base_ref_name if pr_view_base_ref_name == _SAME_AS_REST_BASE_REF else pr_view_base_ref_name
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json
        import os
        import sys

        CALL_LOG = {str(call_log)!r}
        HEAD_REF_OID = {head_ref_oid!r}
        HEAD_REF_OID_SECOND = {head_ref_oid_second!r}
        HEAD_REF_OID_THIRD = {head_ref_oid_third!r}
        BASE_REF_OID = {base_ref_oid!r}
        BASE_REF_NAME = {base_ref_name!r}
        PR_VIEW_BASE_REF_NAME = {view_base_ref_name!r}
        BASE_DEFAULT_BRANCH = {base_default_branch!r}
        FILES = {list(files or [])!r}
        DIFF_TEXT = {resolved_diff_text!r}
        FAIL_PR_VIEW = {fail_pr_view!r}
        FAIL_FILES = {fail_files!r}
        FAIL_DIFF = {fail_diff!r}
        DIFF_ERROR_TEXT = {diff_error_text!r}
        CHANGED_FILES = {len(files or []) if changed_files is None else changed_files!r}
        OMIT_CHANGED_FILES = {omit_changed_files!r}
        FAIL_REST = {fail_rest!r}
        FILES_FAILURE_EXIT_STATUS = {files_failure_exit_status!r}
        PR_LIST_REPLIES = json.loads({json.dumps(pr_list_replies or {})!r})
        JSON_NULL = {_JSON_NULL!r}
        sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})
        from _review_pr_stack_helpers import answer_gh_pr_list

        def null_if_marked(value):
            return None if value == JSON_NULL else value

        {GH_CONTROL_CHARACTER_SANITIZER_SHIM_LINE}
        args = sys.argv[1:]
        prior_pr_view_calls = 0
        if os.path.exists(CALL_LOG):
            with open(CALL_LOG) as f:
                for line in f:
                    if not line.strip():
                        continue
                    if json.loads(line)["args"][:2] == ["pr", "view"]:
                        prior_pr_view_calls += 1
        record = {{"args": args}}
        with open(CALL_LOG, "a") as f:
            f.write(json.dumps(record) + chr(10))
        if args[:2] == ["pr", "list"]:
            sys.exit(answer_gh_pr_list(args[2:], PR_LIST_REPLIES))
        if args[:2] == ["pr", "view"]:
            if FAIL_PR_VIEW:
                sys.exit(1)
            oid = HEAD_REF_OID
            if HEAD_REF_OID_SECOND is not None and prior_pr_view_calls >= 1:
                oid = HEAD_REF_OID_SECOND
            if HEAD_REF_OID_THIRD is not None and prior_pr_view_calls >= 2:
                oid = HEAD_REF_OID_THIRD
            if "--jq" in args:
                if oid:
                    print(oid)
                sys.exit(0)
            reply = {{}}
            if oid:
                reply["headRefOid"] = oid
            if BASE_REF_OID is not None:
                reply["baseRefOid"] = BASE_REF_OID
            if PR_VIEW_BASE_REF_NAME is not None:
                reply["baseRefName"] = null_if_marked(PR_VIEW_BASE_REF_NAME)
            print(json.dumps(reply))
            sys.exit(0)
        if args[:2] == ["pr", "diff"]:
            if FAIL_DIFF:
                if DIFF_ERROR_TEXT:
                    print(DIFF_ERROR_TEXT, file=sys.stderr)
                sys.exit(1)
            sys.stdout.write(DIFF_TEXT)
            sys.exit(0)
        if args[:1] == ["api"] and not args[1].split("?")[0].endswith("/files"):
            if FAIL_REST:
                sys.exit(1)
            payload = {{}} if OMIT_CHANGED_FILES else {{"changed_files": CHANGED_FILES}}
            base_repo = {{}}
            if BASE_DEFAULT_BRANCH is not None:
                base_repo["default_branch"] = null_if_marked(BASE_DEFAULT_BRANCH)
            base = {{"repo": base_repo}}
            if BASE_REF_NAME is not None:
                base["ref"] = null_if_marked(BASE_REF_NAME)
            payload["base"] = base
            print(json.dumps(payload))
            sys.exit(0)
        if args[:1] == ["api"]:
            if "--paginate" not in args or not args[1].endswith("?per_page=100"):
                print("shim: listing call without --paginate and per_page=100", file=sys.stderr)
                sys.exit(97)
            if FAIL_FILES:
                sys.exit(FILES_FAILURE_EXIT_STATUS)
            encode = (
                (lambda name: json.dumps(name, ensure_ascii=False))
                if ".[].filename | @json" in args else str
            )
            for f in FILES:
                sys.stdout.buffer.write((encode(sanitize_like_gh(f)) + chr(10)).encode("utf-8"))
            sys.exit(0)
        sys.exit(0)
    """)


def _read_calls(call_log: Path) -> list[list[str]]:
    if not call_log.exists():
        return []
    return [json.loads(line)["args"] for line in call_log.read_text().splitlines() if line]


def _local_pr_ref_names(repo: Path) -> str:
    return subprocess.run(
        ["git", "for-each-ref", "refs/review-pr"], cwd=repo, capture_output=True, text=True, check=True,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    ).stdout.strip()


def _review_worktrees(repo: Path) -> list[Path]:
    """Every review-pr-* entry under repo's .claude/worktrees, the directory
    review-pr-checkout.sh names its worktrees in."""
    worktrees_dir = repo / ".claude" / "worktrees"
    return sorted(worktrees_dir.glob("review-pr-*")) if worktrees_dir.is_dir() else []


def _printed_diff_path(result: subprocess.CompletedProcess) -> Path:
    """The diff file's path, which is stdout line 1 of a successful run."""
    return Path(result.stdout.splitlines()[0])


def _printed_base_ref_oid(result: subprocess.CompletedProcess) -> str:
    """The base commit's full hex object name, which is stdout line 2 when the base fetch succeeded."""
    return result.stdout.splitlines()[1]


# Stdout line 3, printed after a successful base fetch only when the PR's base branch is the repository's default branch.
_BASE_IS_DEFAULT_BRANCH_TOKEN = "BASE_IS_DEFAULT_BRANCH"


def _run(
    cwd: Path,
    home: Path,
    args: list[str],
    tmp_path: Path,
    *,
    head_ref_oid: str | None = None,
    files: list[str] | None = None,
    diff_text: str | None = None,
    fail_pr_view: bool = False,
    fail_files: bool = False,
    head_ref_oid_second: str | None = None,
    fail_diff: bool = False,
    diff_error_text: str | None = None,
    changed_files: int | str | None = None,
    omit_changed_files: bool = False,
    fail_rest: bool = False,
    files_failure_exit_status: int = 1,
    head_ref_oid_third: str | None = None,
    base_ref_oid: str | None = _BASE_FROM_ORIGIN_MAIN,
    base_ref_name: str | None = "main",
    base_default_branch: str | None = "main",
    pr_view_base_ref_name: str | None = _SAME_AS_REST_BASE_REF,
    pr_list_replies: dict | None = None,
    extra_env: dict | None = None,
    path_prefix: Path | None = None,
    script_path: Path = SCRIPT,
) -> tuple[subprocess.CompletedProcess, Path]:
    """Runs the script. `script_path` names a copy to run in its place. `base_ref_oid` defaults to the commit origin/main
    points at in `cwd`, which origin already holds, so a test that does not care
    about the base still reaches a successful base fetch. None omits baseRefOid
    from the gh reply. `base_ref_name` and `base_default_branch` default to the
    same branch, and None drops that field from the REST payload.
    `pr_view_base_ref_name` is the `baseRefName` the `gh pr view` reply
    reports, defaulting to `base_ref_name`; None drops it. `path_prefix`
    puts a shim directory ahead of the gh shim on PATH."""
    _seed_session(home, SID)
    call_log = tmp_path / "gh_calls.jsonl"
    if base_ref_oid == _BASE_FROM_ORIGIN_MAIN:
        base_ref_oid = _origin_main_sha(cwd)
    env = {
        **_shimmed_env(
            tmp_path,
            _gh_shim_source(
                call_log,
                head_ref_oid=head_ref_oid,
                files=files,
                diff_text=diff_text,
                fail_pr_view=fail_pr_view,
                fail_files=fail_files,
                head_ref_oid_second=head_ref_oid_second,
                fail_diff=fail_diff,
                diff_error_text=diff_error_text,
                changed_files=changed_files,
                omit_changed_files=omit_changed_files,
                fail_rest=fail_rest,
                files_failure_exit_status=files_failure_exit_status,
                head_ref_oid_third=head_ref_oid_third,
                base_ref_oid=base_ref_oid,
                base_ref_name=base_ref_name,
                base_default_branch=base_default_branch,
                pr_view_base_ref_name=pr_view_base_ref_name,
                pr_list_replies=pr_list_replies,
            ),
        ),
        "HOME": str(home),
    }
    env.pop("CLAUDE_CONFIG_DIR", None)
    if extra_env:
        env.update(extra_env)
    if path_prefix is not None:
        env["PATH"] = f"{path_prefix}{os.pathsep}{env['PATH']}"
    result = subprocess.run(
        ["bash", str(script_path), *args], cwd=cwd, env=env, capture_output=True, text=True,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    )
    _assert_gh_calls_are_read_only(
        _read_calls(call_log), known_api_endpoint=_KNOWN_API_ENDPOINT, allowed_pr_subcommands=("view", "diff", "list")
    )
    return result, call_log


class TestUsageErrors:
    @pytest.mark.parametrize("args", [[], ["foo/bar#42", "extra"], ["no-hash-or-slash"], ["../..#5"]])
    def test_invalid_argv_exits_two_with_no_gh_call(self, isolated_home, repo_with_pr_ref, tmp_path, args):
        repo, _ = repo_with_pr_ref
        result, call_log = _run(repo, isolated_home, args, tmp_path)
        assert result.returncode == 2
        assert _read_calls(call_log) == []


class TestOriginMismatch:
    def test_owner_repo_differing_from_origin_aborts_before_any_gh_call(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [f"decoy-owner/decoy-repo#{PR_NUMBER}"], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 2, result.stderr
        assert "origin" in result.stderr
        assert _read_calls(call_log) == []


class TestOriginCaseInsensitiveMatch:
    def test_pr_identity_case_differing_from_origin_still_produces_a_diff(self, isolated_home, tmp_path):
        """GitHub treats owner/repo slugs case-insensitively, so a PR identity
        spelled with different case than origin's own stored URL case must
        match, not be misread as a cross-repo target and refused by
        TestOriginMismatch's own check above."""
        origin_owner_repo = "Foo-Org/Bar-Repo"
        repo, pr_sha = _build_repo_with_pr_ref(tmp_path, owner_repo=origin_owner_repo, pr_number=PR_NUMBER)
        _install_audit_script(isolated_home)
        result, _ = _run(
            repo, isolated_home, [f"foo-org/bar-repo#{PR_NUMBER}"], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 0, result.stderr
        diff_path = _printed_diff_path(result)
        assert diff_path == isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.diff"
        assert diff_path.exists()


class TestInitialHeadRefOidFetchFailure:
    def test_failed_initial_headrefoid_fetch_aborts_before_any_artifact_is_written(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, _ = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, fail_pr_view=True, files=["a.py"],
        )
        assert result.returncode == 2, result.stderr
        assert "could not fetch" in result.stderr
        assert "headRefOid" in result.stderr
        assert result.stdout == ""
        active_dir = isolated_home / ".claude" / ".review-pr-active.d"
        assert not active_dir.exists() or list(active_dir.glob(f"{SID}.*")) == []
        assert not any(c[:2] == ["pr", "diff"] for c in _read_calls(call_log))


class TestMissingAuditScript:
    def test_uninstalled_skill_directory_aborts_before_any_fetch(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"]
        )
        assert result.returncode == 2, result.stderr
        assert "audit script not found" in result.stderr
        assert _read_calls(call_log) == []


class TestHeadRefOidDrift:
    def test_headrefoid_change_across_the_two_fetches_aborts(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, head_ref_oid_second="f" * 40, files=["a.py"],
        )
        assert result.returncode == 2, result.stderr
        assert "force-push" in result.stderr
        calls = _read_calls(call_log)
        assert not any(c[:2] == ["pr", "diff"] for c in calls), (
            "a drift-detected abort must never reach the diff fetch"
        )


class TestHeadRefOidDriftDuringDiffFetch:
    def test_headrefoid_change_during_the_diff_fetch_aborts_with_no_diff_or_provenance(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """The first two headRefOid reads agree, so only the re-fetch after
        `gh pr diff` can notice a push that landed while the diff was fetched."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, head_ref_oid_third="f" * 40, files=["a.py"],
        )
        assert result.returncode == 2, result.stderr
        assert "force-push" in result.stderr
        assert "while its diff was being fetched" in result.stderr
        assert result.stdout == ""
        calls = _read_calls(call_log)
        assert any(c[:2] == ["pr", "diff"] for c in calls), "control: the diff was fetched before the drift was noticed"
        active_dir = isolated_home / ".claude" / ".review-pr-active.d"
        assert not active_dir.exists() or list(active_dir.glob(f"{SID}.*")) == []

    def test_a_head_that_does_not_move_during_the_diff_fetch_still_produces_a_diff(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 0, result.stderr
        pr_view_calls = [c for c in _read_calls(call_log) if c[:2] == ["pr", "view"]]
        assert len(pr_view_calls) == 3, "initial read, pre-audit re-fetch, and post-diff re-fetch"


class TestHeadRefOidRecheckThatReturnsNothingFailsClosed:
    """The shim answers a `gh pr view` call it is told to blank with exit 0 and
    no output, the shape of a gh that succeeded without printing the head."""

    def test_an_empty_recheck_after_the_file_list_aborts_before_the_diff_fetch(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, head_ref_oid_second="", files=["a.py"],
        )
        assert result.returncode == 2, result.stderr
        assert "could not re-fetch" in result.stderr
        assert "to confirm the file list above is still current" in result.stderr
        assert result.stdout == ""
        assert not any(c[:2] == ["pr", "diff"] for c in _read_calls(call_log))
        active_dir = isolated_home / ".claude" / ".review-pr-active.d"
        assert not active_dir.exists() or list(active_dir.glob(f"{SID}.*")) == []

    def test_an_empty_recheck_after_the_diff_aborts_with_no_diff_or_provenance(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, head_ref_oid_third="", files=["a.py"],
        )
        assert result.returncode == 2, result.stderr
        assert "could not re-fetch" in result.stderr
        assert "to confirm the diff above is still current" in result.stderr
        assert result.stdout == ""
        assert any(c[:2] == ["pr", "diff"] for c in _read_calls(call_log)), (
            "control: the diff was fetched before the recheck failed"
        )
        active_dir = isolated_home / ".claude" / ".review-pr-active.d"
        assert not active_dir.exists() or list(active_dir.glob(f"{SID}.*")) == []


class TestEmptyDiffIsRefused:
    @pytest.mark.parametrize("empty_diff_text", ["", "\n", "  \n\t\n"], ids=["empty", "newline", "whitespace"])
    def test_an_empty_diff_for_a_pr_with_changed_files_aborts_with_no_diff_or_provenance(
        self, isolated_home, repo_with_pr_ref, tmp_path, empty_diff_text
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], diff_text=empty_diff_text,
        )
        assert result.returncode == 2, result.stderr
        assert "printed no diff although the PR reports 1 changed files" in result.stderr
        assert result.stdout == ""
        active_dir = isolated_home / ".claude" / ".review-pr-active.d"
        assert not active_dir.exists() or list(active_dir.glob(f"{SID}.*")) == []

    def test_an_empty_diff_for_a_pr_with_no_changed_files_is_still_written(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=[], diff_text="",
        )
        assert result.returncode == 0, result.stderr
        assert _printed_diff_path(result).exists()


class TestNoCheckoutNoWorktreeNoLocalRef:
    def test_restricted_pr_produces_a_diff_file_and_diff_only_provenance(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 0, result.stderr

        diff_path = _printed_diff_path(result)
        assert diff_path == isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.diff"
        assert diff_path.exists()
        assert "diff --git a/a.py b/a.py" in diff_path.read_text()

        provenance = isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.provenance"
        fields = _provenance_fields(provenance)
        assert fields["pr_identity"] == PR_IDENTITY
        assert fields["head_ref_oid"] == pr_sha
        assert fields["pid"].isdigit()
        assert fields["mode"] == "diff-only"

        assert _local_pr_ref_names(repo) == "", "diff-only mode must never fetch a local PR ref"
        assert _review_worktrees(repo) == []


_FULL_HEX_OBJECT_NAME = re.compile(r"[0-9a-f]{40}")


def _git_output(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    ).stdout


def _repo_state_snapshot(repo: Path) -> dict[str, str]:
    """HEAD, every ref, the index and the working tree of `repo`, as git reports them."""
    return {
        "head": _git_output(repo, "rev-parse", "HEAD") + _git_output(repo, "symbolic-ref", "-q", "HEAD"),
        "refs": _git_output(repo, "for-each-ref"),
        "index": _git_output(repo, "ls-files", "--stage"),
        "working_tree": _git_output(repo, "status", "--porcelain", "--untracked-files=all")
        + _git_output(repo, "diff"),
    }


def _assert_diff_and_provenance_written(home: Path, result: subprocess.CompletedProcess, head_sha: str) -> None:
    """The path on stdout line 1 holds the shim's `a.py` diff, and the provenance records the head."""
    diff_path = _printed_diff_path(result)
    assert diff_path == home / ".claude" / ".review-pr-active.d" / f"{SID}.diff"
    assert "diff --git a/a.py b/a.py" in diff_path.read_text()
    provenance = home / ".claude" / ".review-pr-active.d" / f"{SID}.provenance"
    fields = _provenance_fields(provenance)
    assert fields["head_ref_oid"] == head_sha
    assert fields["mode"] == "diff-only"


_BASE_REF_OID_JQ_FILTER = ".baseRefOid // empty"
_BASE_REF_NAME_JQ_FILTER = ".baseRefName // empty"
_REST_BASE_REF_NAME_JQ_FILTER = ".base.ref // empty"
_BASE_DEFAULT_BRANCH_JQ_FILTER = ".base.repo.default_branch // empty"


def _assert_refused_before_any_listing_or_diff_fetch(
    result: subprocess.CompletedProcess, home: Path, call_log: Path, *, provenance_pre_seeded: bool = False
) -> None:
    """The run printed nothing, reached no file listing and no `gh pr diff`, and wrote no diff file.
    It wrote no provenance either, unless a test pre-seeded one, which the test checks itself."""
    calls = _read_calls(call_log)
    assert result.stdout == ""
    assert not any(_is_listing_call(call, "/files") for call in calls)
    assert not any(call[:2] == ["pr", "diff"] for call in calls)
    active_dir = home / ".claude" / ".review-pr-active.d"
    assert not (active_dir / f"{SID}.diff").exists()
    if not provenance_pre_seeded:
        assert not (active_dir / f"{SID}.provenance").exists()


def _assert_refused_before_any_lookup_listing_or_artifact(
    result: subprocess.CompletedProcess, home: Path, call_log: Path
) -> None:
    """As _assert_refused_before_any_listing_or_diff_fetch, and the run made no `gh pr list` call."""
    _assert_refused_before_any_listing_or_diff_fetch(result, home, call_log)
    assert not any(call[:2] == ["pr", "list"] for call in _read_calls(call_log))


def _install_jq_shim_that_fails_the_read(tmp_path: Path, failing_jq_filter: str) -> Path:
    """A `jq` PATH shim that exits 1 only for the script's `jq -r` read of
    `failing_jq_filter` and runs the real jq for every other call."""
    real_jq = shutil.which("jq")
    assert real_jq is not None, "the real jq binary is required to delegate to"
    jq_shim_dir = tmp_path / "jq-shim"
    jq_shim_dir.mkdir()
    jq_shim = jq_shim_dir / "jq"
    jq_shim.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import os
        import sys
        if sys.argv[1:] == ["-r", {failing_jq_filter!r}]:
            sys.stdin.buffer.read()
            sys.exit(1)
        os.execv({real_jq!r}, ["jq", *sys.argv[1:]])
    """))
    jq_shim.chmod(0o755)
    return jq_shim_dir


class TestBaseCommitFetch:
    """The script fetches the PR's baseRefOid by object name and prints it as
    stdout line 2, then the default-branch token as line 3 when the base branch
    is the default branch. A base that cannot be fetched degrades only the base."""

    def test_a_base_commit_absent_locally_is_fetched_and_printed_as_a_full_hex_line(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        base_sha = _push_new_base_commit(repo, tmp_path)
        assert not _git_commit_is_present(repo, base_sha), "control: the base commit is absent before the run"

        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], base_ref_oid=base_sha,
        )

        assert result.returncode == 0, result.stderr
        assert _git_commit_is_present(repo, base_sha), "the run fetched the base commit"
        stdout_lines = result.stdout.splitlines()
        assert len(stdout_lines) == 3, "diff path, base object name, then the default-branch token"
        assert stdout_lines[2] == _BASE_IS_DEFAULT_BRANCH_TOKEN
        assert _FULL_HEX_OBJECT_NAME.fullmatch(_printed_base_ref_oid(result))
        assert _printed_base_ref_oid(result) == base_sha
        assert "BASE_UNAVAILABLE" not in result.stderr
        _assert_diff_and_provenance_written(isolated_home, result, pr_sha)
        first_pr_view = next(c for c in _read_calls(call_log) if c[:2] == ["pr", "view"])
        assert "headRefOid,baseRefOid,baseRefName" in first_pr_view
        assert "--jq" not in first_pr_view

    def test_a_base_branch_other_than_the_default_prints_the_base_without_the_token(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        base_sha = _push_new_base_commit(repo, tmp_path)

        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], base_ref_oid=base_sha,
            base_ref_name="feature/stacked", base_default_branch="main",
        )

        assert result.returncode == 0, result.stderr
        assert len(result.stdout.splitlines()) == 2, "the diff path and the base object name alone"
        assert _printed_base_ref_oid(result) == base_sha
        assert _BASE_IS_DEFAULT_BRANCH_TOKEN not in result.stdout

    @pytest.mark.parametrize(
        ("agreed_base_ref_name", "base_default_branch", "expect_token"),
        [("feature/stacked", "main", False), ("feature/stacked", "feature/stacked", True)],
        ids=["agreed-base-differs-from-default", "agreed-base-is-the-default"],
    )
    def test_the_token_follows_the_base_branch_name_both_reads_agree_on(
        self, isolated_home, repo_with_pr_ref, tmp_path, agreed_base_ref_name, base_default_branch, expect_token
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        base_sha = _push_new_base_commit(repo, tmp_path)

        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], base_ref_oid=base_sha,
            base_ref_name=agreed_base_ref_name, base_default_branch=base_default_branch,
        )

        assert result.returncode == 0, result.stderr
        expected_lines_after_diff_path = [base_sha, _BASE_IS_DEFAULT_BRANCH_TOKEN] if expect_token else [base_sha]
        assert result.stdout.splitlines()[1:] == expected_lines_after_diff_path

    @pytest.mark.parametrize(
        ("rest_base_ref_name", "pr_view_base_ref_name"),
        [("main", "feature/stacked"), ("feature/stacked", "main")],
        ids=["gh-pr-view-reports-a-stacked-base", "rest-reports-a-stacked-base"],
    )
    def test_a_base_branch_the_two_reads_disagree_on_exits_2_before_any_lookup_or_listing(
        self, isolated_home, repo_with_pr_ref, tmp_path, rest_base_ref_name, pr_view_base_ref_name
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref

        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"],
            base_ref_name=rest_base_ref_name, base_default_branch="main", pr_view_base_ref_name=pr_view_base_ref_name,
        )

        assert result.returncode == 2
        assert (
            f'reads as "{rest_base_ref_name}" from the REST payload but as "{pr_view_base_ref_name}" from gh pr view'
            in result.stderr
        )
        _assert_refused_before_any_lookup_listing_or_artifact(result, isolated_home, call_log)

    def test_base_branch_names_past_the_echo_limit_are_cut_in_the_disagreement_message(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        limit = _review_pr_lib_integer("REVIEW_PR_STACK_TEXT_ECHO_LIMIT")
        rest_base_ref_name, pr_view_base_ref_name = "r" * (limit + 40), "v" * (limit + 40)
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref

        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], base_ref_name=rest_base_ref_name, base_default_branch="main",
            pr_view_base_ref_name=pr_view_base_ref_name,
        )

        assert result.returncode == 2, result.stderr
        for name in (rest_base_ref_name, pr_view_base_ref_name):
            assert f'"{name[:limit]}...[truncated]"' in result.stderr
            assert name[: limit + 1] not in result.stderr

    @pytest.mark.parametrize("absent_value", [None, "", _JSON_NULL], ids=["absent", "empty", "null"])
    @pytest.mark.parametrize(
        "unreadable_field", ["pr_view_base_ref_name", "base_ref_name", "base_default_branch"],
        ids=["gh-pr-view-base-ref-name", "rest-base-ref", "rest-default-branch"],
    )
    def test_an_unreadable_base_branch_or_default_branch_exits_2_before_any_lookup_or_listing(
        self, isolated_home, repo_with_pr_ref, tmp_path, unreadable_field, absent_value
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        readable_names = {"pr_view_base_ref_name": "main", "base_ref_name": "main", "base_default_branch": "main"}

        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], **{**readable_names, unreadable_field: absent_value},
        )

        assert result.returncode == 2
        assert "so whether the PR is stacked is undecided" in result.stderr
        _assert_refused_before_any_lookup_listing_or_artifact(result, isolated_home, call_log)

    @pytest.mark.parametrize(
        "failing_jq_filter",
        [_BASE_REF_NAME_JQ_FILTER, _REST_BASE_REF_NAME_JQ_FILTER, _BASE_DEFAULT_BRANCH_JQ_FILTER],
        ids=["gh-pr-view-base-ref-name-read", "rest-base-ref-read", "rest-default-branch-read"],
    )
    def test_a_failed_base_branch_read_exits_2_before_any_lookup_or_listing(
        self, isolated_home, repo_with_pr_ref, tmp_path, failing_jq_filter
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        base_sha = _push_new_base_commit(repo, tmp_path)

        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], base_ref_oid=base_sha,
            path_prefix=_install_jq_shim_that_fails_the_read(tmp_path, failing_jq_filter),
        )

        assert result.returncode == 2
        assert "so whether the PR is stacked is undecided" in result.stderr
        _assert_refused_before_any_lookup_listing_or_artifact(result, isolated_home, call_log)

    def test_a_default_branch_with_a_slash_in_its_name_prints_the_token(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        base_sha = _push_new_base_commit(repo, tmp_path)

        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], base_ref_oid=base_sha,
            base_ref_name="release/1.x", base_default_branch="release/1.x",
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines()[1:] == [base_sha, _BASE_IS_DEFAULT_BRANCH_TOKEN]

    def test_a_base_branch_that_is_not_exactly_the_default_prints_no_token(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """A trailing space must survive the name's extraction from the `gh pr view` reply.
        One wiring case; the comparison's near-miss matrix is in test_review_pr_lib.py."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        base_sha = _push_new_base_commit(repo, tmp_path)

        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], base_ref_oid=base_sha,
            base_ref_name="main ", base_default_branch="main",
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines()[1:] == [base_sha]
        assert _BASE_IS_DEFAULT_BRANCH_TOKEN not in result.stdout

    def test_the_base_fetch_leaves_head_refs_index_working_tree_and_fetch_head_untouched(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        base_sha = _push_new_base_commit(repo, tmp_path)
        state_before = _repo_state_snapshot(repo)
        fetch_head = repo / ".git" / "FETCH_HEAD"
        assert not fetch_head.exists(), "control: no fetch has written FETCH_HEAD yet"

        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], base_ref_oid=base_sha,
        )

        assert result.returncode == 0, result.stderr
        assert _git_commit_is_present(repo, base_sha), "the base fetch ran"
        assert _repo_state_snapshot(repo) == state_before
        assert not fetch_head.exists(), "the base fetch must not write FETCH_HEAD"

        subprocess.run(
            ["git", "fetch", "origin", base_sha], cwd=repo, capture_output=True, check=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
        assert fetch_head.exists(), "control: a plain fetch of the same object name does write FETCH_HEAD"

    def test_a_fetch_that_exits_zero_without_the_commit_degrades_the_base(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        base_sha = _push_new_base_commit(repo, tmp_path)
        shim_dir = _git_shim_that_fails_on_subcommand(
            tmp_path, f"*fetch --no-write-fetch-head --recurse-submodules=no -- origin {base_sha}", exit_status=0
        )

        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], base_ref_oid=base_sha, path_prefix=shim_dir,
        )

        assert result.returncode == 0, result.stderr
        assert _shim_match_count(shim_dir) == 1, "control: the base fetch reached git and exited 0"
        assert not _git_commit_is_present(repo, base_sha), "control: the shimmed fetch left the commit absent"
        assert "BASE_UNAVAILABLE fetch-failed" in result.stderr
        assert len(result.stdout.splitlines()) == 1, "the unfetched object name is never offered on line 2"
        assert _BASE_IS_DEFAULT_BRANCH_TOKEN not in result.stdout, "the token needs the base fetch"
        _assert_diff_and_provenance_written(isolated_home, result, pr_sha)

    def test_an_option_shaped_base_ref_oid_is_never_passed_to_git_fetch(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """The input matrix for review_pr_is_full_hex_oid lives in
        test_review_pr_lib.py. This pins that the script runs the check before
        the value reaches a `git fetch` argument."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        upload_pack_ran_sentinel = tmp_path / "upload-pack-ran"
        upload_pack_script = tmp_path / "upload-pack.sh"
        upload_pack_script.write_text(f"#!/bin/sh\ntouch '{upload_pack_ran_sentinel}'\nexec git upload-pack \"$@\"\n")
        upload_pack_script.chmod(0o755)
        option_shaped_base_ref_oid = f"--upload-pack={upload_pack_script}"

        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], base_ref_oid=option_shaped_base_ref_oid,
        )

        assert result.returncode == 0, result.stderr
        assert "BASE_UNAVAILABLE not-hex" in result.stderr
        assert not upload_pack_ran_sentinel.exists(), "the value must not run as a fetch option"
        assert len(result.stdout.splitlines()) == 1
        _assert_diff_and_provenance_written(isolated_home, result, pr_sha)

        subprocess.run(
            ["git", "fetch", "origin", option_shaped_base_ref_oid],
            cwd=repo, capture_output=True, check=False, timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
        assert upload_pack_ran_sentinel.exists(), "control: an unguarded fetch of the value does run the script"

    @pytest.mark.parametrize("base_ref_oid", [None, ""], ids=["field-absent", "field-empty"])
    def test_a_missing_base_ref_oid_degrades_the_base_without_a_fetch(
        self, isolated_home, repo_with_pr_ref, tmp_path, base_ref_oid
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        shim_dir = _git_shim_that_fails_on_subcommand(tmp_path, "*fetch*", exit_status=1)

        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], base_ref_oid=base_ref_oid, path_prefix=shim_dir,
        )

        assert result.returncode == 0, result.stderr
        assert "BASE_UNAVAILABLE missing" in result.stderr
        assert _shim_match_count(shim_dir) == 0
        assert len(result.stdout.splitlines()) == 1
        _assert_diff_and_provenance_written(isolated_home, result, pr_sha)

    @pytest.mark.parametrize(
        ("exit_status", "expected_cause"), [(1, "fetch-failed"), (124, "fetch-timed-out")],
        ids=["plain-failure", "cap-kill-status"],
    )
    def test_a_failing_base_fetch_degrades_the_base_and_never_echoes_git_output(
        self, isolated_home, repo_with_pr_ref, tmp_path, exit_status, expected_cause
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        base_sha = _origin_main_sha(repo)
        shim_dir = _git_shim_that_fails_on_subcommand(
            tmp_path, f"*fetch --no-write-fetch-head --recurse-submodules=no -- origin {base_sha}", exit_status=exit_status
        )

        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], base_ref_oid=base_sha, path_prefix=shim_dir,
        )

        assert result.returncode == 0, result.stderr
        assert _shim_match_count(shim_dir) == 1, "control: the base fetch reached git"
        assert f"BASE_UNAVAILABLE {expected_cause}" in result.stderr
        other_cause = "fetch-timed-out" if expected_cause == "fetch-failed" else "fetch-failed"
        assert other_cause not in result.stderr
        assert "synthetic git failure" not in result.stderr, "git's own output must never reach stderr"
        assert len(result.stdout.splitlines()) == 1
        _assert_diff_and_provenance_written(isolated_home, result, pr_sha)

    def test_a_failed_base_ref_oid_read_degrades_the_base_and_keeps_the_head(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"],
            path_prefix=_install_jq_shim_that_fails_the_read(tmp_path, _BASE_REF_OID_JQ_FILTER),
        )
        assert result.returncode == 0, result.stderr
        assert "BASE_UNAVAILABLE missing" in result.stderr
        assert len(result.stdout.splitlines()) == 1
        _assert_diff_and_provenance_written(isolated_home, result, pr_sha)

    def test_a_missing_head_ref_oid_still_aborts_even_with_a_usable_base(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, _ = repo_with_pr_ref
        shim_dir = _git_shim_that_fails_on_subcommand(tmp_path, "*fetch*", exit_status=1)

        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=None, files=["a.py"], path_prefix=shim_dir,
        )

        assert result.returncode == 2, result.stderr
        assert "current headRefOid" in result.stderr
        assert [c[:2] for c in _read_calls(call_log)] == [["pr", "view"]], "the abort comes right after the first read"
        assert "BASE_UNAVAILABLE" not in result.stderr
        assert result.stdout == ""
        assert _shim_match_count(shim_dir) == 0

    def test_a_degraded_base_followed_by_a_failing_diff_fetch_still_aborts_with_empty_stdout(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref

        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], base_ref_oid=None, fail_diff=True,
        )

        assert result.returncode == 2, result.stderr
        assert "BASE_UNAVAILABLE missing" in result.stderr, "control: the base degraded before the diff failed"
        assert "gh pr diff failed or timed out" in result.stderr
        assert result.stdout == ""
        assert not (isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.diff").exists()


class TestBaseFetchRunsNoGitHooks:
    def test_no_git_hook_runs_during_the_base_fetch(self, isolated_home, repo_with_pr_ref, tmp_path):
        """The argv test below is the version-independent pin of the base fetch's
        `-c core.hooksPath=/dev/null`. This sentinel corroborates it on a git that
        fires `reference-transaction` for a fetch by object name, and skips on one
        that does not. A separate clone fetching the same commit with hooks
        enabled is the control."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        control_repo, hook_ran_sentinel, control_hook_ran_sentinel = _install_reference_transaction_sentinel_hooks(
            repo, tmp_path
        )
        base_sha = _push_new_base_commit(repo, tmp_path)

        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], base_ref_oid=base_sha,
        )

        assert result.returncode == 0, result.stderr
        assert _git_commit_is_present(repo, base_sha), "the base fetch ran"
        assert not hook_ran_sentinel.exists(), "the base fetch must run with git hooks disabled"
        _skip_unless_a_hooks_enabled_fetch_by_object_name_fires_reference_transaction(
            control_repo, base_sha, control_hook_ran_sentinel
        )

    def test_the_base_fetch_passes_the_hooks_off_flag(self, isolated_home, repo_with_pr_ref, tmp_path):
        """Pins `-c core.hooksPath=/dev/null` by recorded argv, which holds on every git version."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        base_sha = _push_new_base_commit(repo, tmp_path)
        shim_dir = _git_shim_that_logs_terminal_prompt_on_fetch(tmp_path)

        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], base_ref_oid=base_sha, path_prefix=shim_dir,
        )

        assert result.returncode == 0, result.stderr
        assert _git_commit_is_present(repo, base_sha), "the base fetch ran"
        fetch_argument_lines = _fetch_argument_lines(shim_dir)
        assert len(fetch_argument_lines) == 1, "control: the base fetch ran"
        fetch_tokens = fetch_argument_lines[0].split(" ")
        assert "core.hooksPath=/dev/null" in fetch_tokens, fetch_argument_lines[0]
        hooks_path_index = fetch_tokens.index("core.hooksPath=/dev/null")
        assert fetch_tokens[hooks_path_index - 1] == "-c", fetch_argument_lines[0]
        assert fetch_tokens[hooks_path_index + 1] == "fetch", fetch_argument_lines[0]


class TestBaseFetchNeverPromptsForCredentials:
    def test_the_base_fetch_disables_the_terminal_credential_prompt(self, isolated_home, repo_with_pr_ref, tmp_path):
        """The base-commit fetch sees GIT_TERMINAL_PROMPT=0 over an ambient 1, with GIT_ASKPASS/SSH_ASKPASS unchanged."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        base_sha = _push_new_base_commit(repo, tmp_path)
        shim_dir = _git_shim_that_logs_terminal_prompt_on_fetch(tmp_path)

        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], base_ref_oid=base_sha, path_prefix=shim_dir,
            extra_env={
                "GIT_TERMINAL_PROMPT": "1", "GIT_ASKPASS": "/ambient/git-askpass", "SSH_ASKPASS": "/ambient/ssh-askpass",
            },
        )

        assert result.returncode == 0, result.stderr
        assert _fetch_terminal_prompt_values(shim_dir) == ["0"]
        assert _fetch_askpass_values(shim_dir) == ["/ambient/git-askpass /ambient/ssh-askpass"]


class TestBaseFetchSkipsSubmoduleRecursion:
    """The base-commit fetch is invoked with --recurse-submodules=no."""

    def test_the_base_fetch_skips_submodule_recursion(self, isolated_home, repo_with_pr_ref, tmp_path):
        """A populated submodule must not make a correct fetch fail, so the fetch opts out of recursing into it."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        base_sha = _push_new_base_commit(repo, tmp_path)
        shim_dir = _git_shim_that_logs_terminal_prompt_on_fetch(tmp_path)

        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], base_ref_oid=base_sha, path_prefix=shim_dir,
        )

        assert result.returncode == 0, result.stderr
        assert _git_commit_is_present(repo, base_sha), "the base fetch ran"
        fetch_argument_lines = _fetch_argument_lines(shim_dir)
        assert len(fetch_argument_lines) == 1, "control: the base fetch ran"
        assert "--recurse-submodules=no" in fetch_argument_lines[0].split(" "), fetch_argument_lines[0]


class TestArtifactCleanup:
    def test_finish_and_clear_stale_each_leave_no_artifact_behind(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 0, result.stderr

        _assert_finish_and_clear_stale_each_empty_the_active_directory(isolated_home, repo, SID)


class TestActiveDirectoryCreationFailure:
    def test_a_file_where_the_active_directory_belongs_aborts_with_exit_two_and_writes_no_diff(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """`mkdir -p` cannot create a directory over a regular file. Under
        `set -e` an unguarded call would exit with mkdir's own status 1."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        active_path = isolated_home / ".claude" / ".review-pr-active.d"
        active_path.parent.mkdir(exist_ok=True)
        active_path.write_text("not a directory\n")
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 2
        assert "could not create the active directory" in result.stderr
        assert result.stdout == ""
        assert active_path.read_text() == "not a directory\n", "pre-existing file is left untouched"


class TestAuditHitReportedNotFatal:
    def test_execution_surface_hit_is_reported_on_stderr_but_exits_zero(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """Distinct from review-pr-checkout.sh's own audit stop: nothing is
        checked out here, so the audit predicate has no subject to
        protect, and its hit becomes a mandatory pre-seeded finding
        instead."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py", ".mcp.json"],
        )
        assert result.returncode == 0, result.stderr
        assert "AUDIT_FINDING" in result.stderr
        assert ".mcp.json" in result.stderr
        diff_path = _printed_diff_path(result)
        assert diff_path.exists()


class TestAuditRunsIsolatedFromPythonEnvironment:
    def test_a_json_module_planted_on_pythonpath_cannot_forge_a_clean_verdict(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """The audit gate runs `python3 -I`, so PYTHONPATH never reaches it. A
        plain `python3` would import the planted json and drop the finding for
        the stop-worthy `.mcp.json`."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        sentinel = tmp_path / "poison-ran"
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py", ".mcp.json"],
            extra_env=_pythonpath_env_that_forges_a_clean_audit(tmp_path / "poison", sentinel),
        )
        assert result.returncode == 0, result.stderr
        assert "AUDIT_FINDING" in result.stderr
        assert not sentinel.exists(), "the planted json module ran inside the audit"


class TestAuditFindingKeepsEachMatchOnOneLine:
    def test_a_matched_path_holding_a_newline_is_printed_json_escaped(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """A PR file name is attacker-chosen, so a newline in a matched path
        must not start a second line that reads as the script's own output."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        injecting_name = "x\nreview-pr-diff.sh: injected line/CLAUDE.md"
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py", injecting_name],
        )
        assert result.returncode == 0, result.stderr
        assert json.dumps(injecting_name) in result.stderr
        assert "\nreview-pr-diff.sh: injected line" not in result.stderr

    def test_a_matched_path_past_the_length_bound_is_cut_and_marked(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        path_limit = _review_pr_lib_integer("REVIEW_PR_AUDIT_PATH_ECHO_LIMIT")
        long_name = "d" * (path_limit + 100) + "/CLAUDE.md"
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=[long_name],
        )
        assert result.returncode == 0, result.stderr
        assert long_name not in result.stderr
        assert f'"{"d" * path_limit}...[truncated]"' in result.stderr

    def test_a_stop_document_that_cannot_be_formatted_prints_a_fixed_line_with_the_match_count(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """The unformattable document is never echoed: `[1, 2]` has no `path`
        field to print, and its own text must not reach stderr."""
        _install_audit_script_that_runs(
            isolated_home,
            "import sys\nprint('{\"stop\": true, \"matches\": [1, 2]}')\nsys.exit(1)\n",
        )
        repo, pr_sha = repo_with_pr_ref
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 0, result.stderr
        assert "AUDIT_FINDING" in result.stderr
        assert (
            "the audit reported 2 matched path(s), but its match list could not be formatted for display"
            in result.stderr
        )
        assert "[1, 2]" not in result.stderr


class TestAuditThatReturnsNoVerdictIsNotAFinding:
    """A finding means the audit positively returned `stop: true`. A tool that
    failed to run aborts with exit 2 and the audit's stderr shown, so a clean
    PR is never reported as blocked because python3 is missing."""

    @pytest.mark.parametrize(
        "audit_script_body",
        [
            "import sys\nsys.exit(127)\n",
            "raise RuntimeError('audit-tool-crashed')\n",
            "import os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n",
            "import sys\nprint('not json')\nsys.exit(1)\n",
            "import sys\nprint('{\"stop\": false, \"matches\": []}')\nsys.exit(1)\n",
        ],
        ids=["python3-missing-status", "uncaught-exception", "sigkill", "status-1-non-json", "status-1-stop-false"],
    )
    def test_a_failed_audit_aborts_with_no_finding_and_no_diff(
        self, isolated_home, repo_with_pr_ref, tmp_path, audit_script_body
    ):
        _install_audit_script_that_runs(isolated_home, audit_script_body)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 2, result.stderr
        assert "returned no verdict (exit " in result.stderr
        assert "AUDIT_FINDING" not in result.stderr
        assert result.stdout == ""
        assert not any(c[:2] == ["pr", "diff"] for c in _read_calls(call_log))

    def test_the_failed_audits_stderr_is_shown(self, isolated_home, repo_with_pr_ref, tmp_path):
        _install_audit_script_that_runs(isolated_home, "raise RuntimeError('audit-tool-crashed')\n")
        repo, pr_sha = repo_with_pr_ref
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 2
        assert "audit-tool-crashed" in result.stderr

    def test_a_positive_stop_verdict_is_reported_as_a_finding_and_the_diff_is_still_written(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script_that_runs(
            isolated_home,
            "import sys\n"
            "print('{\"stop\": true, \"matches\": [{\"path\": \"a.py\", \"reason\": \"stand-in reason\"}]}')\n"
            "sys.exit(1)\n",
        )
        repo, pr_sha = repo_with_pr_ref
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 0, result.stderr
        assert "AUDIT_FINDING" in result.stderr
        assert '"a.py": stand-in reason' in result.stderr
        assert _printed_diff_path(result).exists()


class TestAuditThatExitsCleanWithoutAVerdictIsNotClean:
    """Exit status 0 alone is not a clean audit: an empty or truncated audit
    script also exits 0, and reporting nothing would read as "audited, nothing
    matched". Only the audit's exact clean document proceeds to the diff."""

    @pytest.mark.parametrize(
        "audit_script_body,first_stdout_line",
        [
            ("", ""),
            ("print('not json')\nprint('second line')\n", "not json"),
            ("print('{\"stop\": true, \"matches\": []}')\n", '{"stop": true, "matches": []}'),
        ],
        ids=["empty-script", "non-json-stdout", "stop-true-at-status-0"],
    )
    def test_a_zero_status_audit_without_a_clean_verdict_aborts_and_writes_no_diff(
        self, isolated_home, repo_with_pr_ref, tmp_path, audit_script_body, first_stdout_line
    ):
        _install_audit_script_that_runs(isolated_home, audit_script_body)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 2, result.stderr
        assert f"returned no verdict (exit 0, first line of stdout: {first_stdout_line})" in result.stderr
        assert "second line" not in result.stderr
        assert "AUDIT_FINDING" not in result.stderr
        assert result.stdout == ""
        assert not any(c[:2] == ["pr", "diff"] for c in _read_calls(call_log))
        assert not list((isolated_home / ".claude" / ".review-pr-active.d").glob("*.diff"))


class TestDiffTextIsWrittenAsFetched:
    def test_a_diff_whose_header_count_differs_from_the_file_count_is_still_written(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """A file changing between a regular file and a symlink appears as two
        `diff --git` headers for one path, so the header count is not a
        completeness signal and no check counts them."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        typechange_diff = _diff_text_for(["link.txt", "link.txt"])
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["link.txt"], diff_text=typechange_diff,
        )
        assert result.returncode == 0, result.stderr
        assert _printed_diff_path(result).read_text().count("diff --git a/link.txt b/link.txt") == 2


class TestChangedFilesPrecheck:
    """`gh pr diff` cannot serve a PR past 300 changed files, so the script
    stops with a named message before fetching a listing or a diff."""

    def test_over_three_hundred_changed_files_stops_before_the_listing_and_the_diff(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], changed_files=301,
        )
        assert result.returncode == 2, result.stderr
        assert "301" in result.stderr and "300" in result.stderr
        calls = _read_calls(call_log)
        assert not any(_is_listing_call(c, "/files") for c in calls)
        assert not any(c[:2] == ["pr", "diff"] for c in calls)

    def test_exactly_three_hundred_changed_files_still_produces_a_diff(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        three_hundred_files = [f"src/file_{n}.py" for n in range(300)]
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=three_hundred_files,
        )
        assert result.returncode == 0, result.stderr
        assert _printed_diff_path(result).exists()

    def test_missing_changed_files_count_aborts_before_the_listing(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], omit_changed_files=True,
        )
        assert result.returncode == 2, result.stderr
        assert "changed_files" in result.stderr
        assert not any(_is_listing_call(c, "/files") for c in _read_calls(call_log))

    def test_rest_payload_fetch_failure_aborts(self, isolated_home, repo_with_pr_ref, tmp_path):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], fail_rest=True,
        )
        assert result.returncode == 2, result.stderr
        assert not any(c[:2] == ["pr", "diff"] for c in _read_calls(call_log))


class TestFileListIntegrity:
    def test_listing_shorter_than_changed_files_aborts_before_the_diff_fetch(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], changed_files=2,
        )
        assert result.returncode == 2
        assert "truncated, or the PR changed while it was being fetched" in result.stderr
        assert "Retry" in result.stderr
        assert not any(c[:2] == ["pr", "diff"] for c in _read_calls(call_log))

    @pytest.mark.parametrize(
        "exit_status,status_wording",
        [(1, "failed (exit 1)"), (124, "timed out"), (137, "failed (exit 137)")],
        ids=["gh-failed", "cap-kill-124", "sigkill-137"],
    )
    def test_listing_failure_aborts_before_the_diff_fetch(
        self, isolated_home, repo_with_pr_ref, tmp_path, exit_status, status_wording
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], fail_files=True, files_failure_exit_status=exit_status,
        )
        assert result.returncode == 2
        assert f"gh api --paginate {status_wording}" in result.stderr
        assert not any(c[:2] == ["pr", "diff"] for c in _read_calls(call_log))

    def test_listing_is_fetched_paginated_at_the_maximum_page_size_one_json_string_per_name(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 0, result.stderr
        files_calls = [c for c in _read_calls(call_log) if _is_listing_call(c, "/files")]
        assert len(files_calls) == 1
        files_call = files_calls[0]
        assert "--paginate" in files_call
        assert files_call[1].endswith("?per_page=100")
        assert ".[].filename | @json" in files_call

    @pytest.mark.parametrize(
        "listed_name",
        [
            "docs/notes.txt\nevil.sh",
            "src/a^[b.py",
            'docs/caf\u00e9 "notes".md',
        ],
        ids=["newline", "caret", "non-ascii-quote-space"],
    )
    def test_no_name_is_refused_for_its_content_and_each_reaches_the_audit_intact(
        self, isolated_home, repo_with_pr_ref, tmp_path, listed_name
    ):
        """This path reports audit hits rather than stopping on them, and
        nothing here splits names on a delimiter, so no name is refused. A
        name under an audited path is echoed by the audit finding, which
        shows the decoded name arrived whole."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        audited_name = f".claude/hooks/{listed_name}"
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py", audited_name],
        )
        assert result.returncode == 0, result.stderr
        assert "AUDIT_FINDING" in result.stderr
        # The finding prints every non-ASCII character as a `\uXXXX` escape, as json.dumps does by default.
        assert json.dumps(audited_name) in result.stderr


class TestGhFailureNeverBypassesTheScrub:
    """A `gh` failure's stderr/error output must never be captured verbatim
    into the `.diff` file -- GitHub API error payloads occasionally echo
    request parameters, and this posting-adjacent artifact is not covered
    by deny-private-project-refs.sh's own gated command set."""

    def test_gh_pr_diff_failure_error_text_never_reaches_the_diff_file_or_output(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        secret_shaped_text = "leaked-token=ghp_" + "AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHHIIII"
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], fail_diff=True, diff_error_text=secret_shaped_text,
        )
        assert result.returncode == 2, result.stderr
        assert secret_shaped_text not in result.stdout
        assert secret_shaped_text not in result.stderr
        diff_path = isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.diff"
        assert not diff_path.exists()


_STACKED_BASE_BRANCH = "feature/stacked"
_LOWER_BASE_BRANCH = "feature/lower"


class TestStackedBaseGate:
    """A PR whose base is not the default branch is refused (exit 4) while an ancestor PR has no submitted review."""

    def test_an_unreviewed_ancestor_exits_4_naming_the_lowest_one_and_leaves_a_pre_seeded_provenance_unchanged(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        provenance = write_review_pr_provenance(isolated_home, PR_IDENTITY, pr_sha, 999, mode="acquired", session_id=SID)
        provenance_before = provenance.read_bytes()

        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], base_ref_name=_STACKED_BASE_BRANCH,
            pr_list_replies=stack_replies(_STACKED_BASE_BRANCH, _LOWER_BASE_BRANCH, lower_reviewed=False),
        )

        assert result.returncode == 4, result.stderr
        assert f"PR {OWNER_REPO}#{STACK_LOWER_PR_NUMBER}, which has no submitted review" in result.stderr
        # Wording tripwires: the next two lines pin refusal prose, not behavior.
        assert "clears once a review is submitted on it on GitHub" in result.stderr
        assert "a draft (pending, unsubmitted) review or one kept local does not count" in result.stderr
        assert "review-pr-checkout.sh refuses" in result.stderr
        assert len(pr_list_calls(_read_calls(call_log))) == 2
        assert provenance.read_bytes() == provenance_before
        _assert_refused_before_any_listing_or_diff_fetch(result, isolated_home, call_log, provenance_pre_seeded=True)

    def test_an_unreviewed_immediate_base_exits_4_naming_it_though_the_ancestor_below_it_is_reviewed(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref

        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], base_ref_name=_STACKED_BASE_BRANCH,
            pr_list_replies=stack_replies(
                _STACKED_BASE_BRANCH, _LOWER_BASE_BRANCH, lower_reviewed=True, upper_reviewed=False
            ),
        )

        assert result.returncode == 4, result.stderr
        assert f"PR {OWNER_REPO}#{STACK_UPPER_PR_NUMBER}, which has no submitted review" in result.stderr
        assert len(pr_list_calls(_read_calls(call_log))) == 2
        _assert_refused_before_any_listing_or_diff_fetch(result, isolated_home, call_log, provenance_pre_seeded=False)

    def test_a_pr_into_the_default_branch_makes_no_lookup_whatever_a_reply_keyed_by_the_default_branch_holds(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        bait_replies = {"main": [pr_list_item(99, "main", "elsewhere")]}

        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], pr_list_replies=bait_replies,
        )

        assert result.returncode == 0, result.stderr
        calls = _read_calls(call_log)
        assert pr_list_calls(calls) == []
        assert any(call[:2] == ["pr", "diff"] for call in calls), "control: the run went on to fetch the diff"

    def test_a_stack_whose_ancestors_all_have_reviews_proceeds_looking_up_one_branch_per_level(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref

        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], base_ref_name=_STACKED_BASE_BRANCH,
            pr_list_replies=stack_replies(_STACKED_BASE_BRANCH, _LOWER_BASE_BRANCH, lower_reviewed=True),
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines()[1:] == [_origin_main_sha(repo)], "no default-branch token for a stacked base"
        calls = _read_calls(call_log)
        assert [call[call.index("-R") + 1] for call in pr_list_calls(calls)] == [OWNER_REPO] * 2
        assert pr_list_head_values(calls) == [_STACKED_BASE_BRANCH, _LOWER_BASE_BRANCH]

    def test_a_walk_that_cannot_decide_exits_2_even_when_gh_exits_4(self, isolated_home, repo_with_pr_ref, tmp_path):
        """gh exits 4 when a command needs authentication, and only the gate itself may produce this script's exit 4."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref

        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], base_ref_name=_STACKED_BASE_BRANCH,
            pr_list_replies={_STACKED_BASE_BRANCH: {"exit": 4}},
        )

        assert result.returncode == 2
        (failure_line,) = result.stderr.splitlines()
        assert failure_line.startswith("review-pr-diff.sh: ")
        assert "failed (exit 4)" in failure_line
        assert len(pr_list_calls(_read_calls(call_log))) == 1
        _assert_refused_before_any_listing_or_diff_fetch(result, isolated_home, call_log)

    def test_a_gh_exit_4_outside_the_walk_exits_2(self, isolated_home, repo_with_pr_ref, tmp_path):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref

        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], base_ref_name=_STACKED_BASE_BRANCH,
            fail_files=True, files_failure_exit_status=4,
        )

        assert result.returncode == 2
        assert "gh api --paginate failed (exit 4)" in result.stderr
        assert len(pr_list_calls(_read_calls(call_log))) == 1, "control: the walk cleared the stack before the listing failed"

    @pytest.mark.parametrize(
        "walk_stdout", ["surprise", "", "0", "007", "[]", "41x", "41\nrm -rf"],
        ids=["unrecognized-text", "empty", "zero", "leading-zero-number", "json-array", "number-then-letter", "number-then-line"],
    )
    def test_walk_output_that_is_neither_clear_nor_an_ancestor_number_exits_2_writing_no_provenance(
        self, isolated_home, repo_with_pr_ref, tmp_path, walk_stdout
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        script_with_stubbed_walk = copy_script_with_stubbed_walk(SCRIPT, tmp_path / "sandbox", walk_stdout)

        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], base_ref_name=_STACKED_BASE_BRANCH, script_path=script_with_stubbed_walk,
        )

        assert result.returncode == 2
        assert "printed neither clear nor an ancestor PR number" in result.stderr
        _assert_refused_before_any_listing_or_diff_fetch(result, isolated_home, call_log)
