"""Tests for review-pr-checkout.sh -- the single script that runs
/review-pr Step 2's stop-before-checkout audit: it re-derives the PR's file
list and headRefOid itself from `gh`/git rather than trusting either as an
argument.

The `gh` CLI is replaced by a PATH shim that records every invocation it
receives (one JSON object per line), matching test_review_pr_post.py's own
shim pattern -- git is left real (not shimmed), so the fetch/checkout
assertions below check actual repository state (a local ref landing, a
worktree materializing) rather than a mocked call.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    SCRIPTS_DIR,
    SKILLS_DIR,
    write_review_pr_provenance,
)

from .conftest import (
    GH_CONTROL_CHARACTER_SANITIZER_SHIM_LINE,
    _assert_finish_and_clear_stale_each_empty_the_active_directory,
    _assert_gh_calls_are_read_only,
    _build_repo_with_pr_ref,
    _git_shim_that_fails_on_worktree_subcommand,
    _install_audit_script,
    _install_audit_script_that_runs,
    _provenance_fields,
    _pythonpath_env_that_forges_a_clean_audit,
    _review_pr_audit_limit,
    _seed_session,
    _shimmed_env,
)

SCRIPT = SCRIPTS_DIR / "review-pr-checkout.sh"
OWNER_REPO = "foo/bar"
PR_NUMBER = "42"
PR_IDENTITY = f"{OWNER_REPO}#{PR_NUMBER}"
SID = "test-session-review-pr-checkout"
_ATTRIBUTION_TRAILER = "🤖 Generated with [Claude Code](https://claude.com/claude-code)"

# A harness bound so a hung bash or interpreter fails one test instead of the suite.
_SUBPROCESS_TIMEOUT_SECONDS = 60

# The only `gh api` endpoints the script reads: the PR resource and its file listing.
_KNOWN_API_ENDPOINT = re.compile(r"repos/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+/pulls/[0-9]+(/files\?per_page=100)?")


def _origin_main_sha(repo: Path) -> str | None:
    """The commit origin/main points at in repo, or None when repo has no
    such ref (a test whose cwd is not the PR repo)."""
    resolved = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "origin/main"], cwd=repo, capture_output=True, text=True,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    )
    return resolved.stdout.strip() or None


def _independent_git_diff(
    repo: Path, base_sha: str, head_sha: str, isolated_home: Path, extra_env: dict | None = None
) -> subprocess.CompletedProcess:
    """The byte oracle for a diff file: a plain `git diff --no-color` over the
    three-dot range, run without the contributor's global or system git
    config or an ambient GIT_DIFF_OPTS, so no diff.* or color.* setting and no
    inherited context override can change what it prints. extra_env applies
    last, so a test can set GIT_DIFF_OPTS on purpose."""
    return subprocess.run(
        ["git", "diff", "--no-color", f"{base_sha}...{head_sha}", "--"],
        cwd=repo,
        env={
            **{name: value for name, value in os.environ.items() if name != "GIT_DIFF_OPTS"},
            "HOME": str(isolated_home),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            **(extra_env or {}),
        },
        capture_output=True,
        check=True,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    )


def _push_pr_head_commit(repo: Path, files: dict[str, str]) -> tuple[str, str]:
    """Replaces origin's PR ref with one commit that writes `files` (path to
    content) on top of origin/main, then resets repo's checkout back to
    origin/main so the PR's content is absent from the main tree. Returns
    (base_sha, head_sha)."""
    base_sha = _origin_main_sha(repo)
    for path, content in files.items():
        (repo / path).write_text(content)
        subprocess.run(["git", "add", path], cwd=repo, check=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS)
    subprocess.run(["git", "commit", "-q", "-m", "pr head commit"], cwd=repo, check=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    ).stdout.strip()
    subprocess.run(
        ["git", "push", "-q", "origin", f"+HEAD:refs/pull/{PR_NUMBER}/head"], cwd=repo, check=True,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    )
    subprocess.run(["git", "reset", "-q", "--hard", base_sha], cwd=repo, check=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS)
    return base_sha, head_sha


def _printed_worktree_path(result: subprocess.CompletedProcess) -> Path:
    """The worktree path, the first of the two lines a successful run prints."""
    return Path(result.stdout.splitlines()[0])


def _printed_diff_path(result: subprocess.CompletedProcess) -> Path:
    """The diff file path, the second of the two lines a successful run prints."""
    return Path(result.stdout.splitlines()[1])


def _review_worktrees(repo: Path) -> list[Path]:
    """Every review worktree this session's checkout runs created for
    PR_NUMBER under repo's main tree -- each run's directory is named
    review-pr-<session-id>-<number>-<random suffix>."""
    worktrees_dir = repo / ".claude" / "worktrees"
    return sorted(worktrees_dir.glob(f"review-pr-{SID}-{PR_NUMBER}-??????")) if worktrees_dir.is_dir() else []


def _registered_worktree_paths(repo: Path) -> list[Path]:
    """Every worktree path `git worktree list --porcelain` reports for repo,
    resolved so a symlinked temp root compares equal to git's own spelling."""
    listing = subprocess.run(
        ["git", "worktree", "list", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    ).stdout
    return [Path(line.removeprefix("worktree ")).resolve() for line in listing.splitlines() if line.startswith("worktree ")]


def _local_pr_ref_names(repo: Path) -> str:
    return subprocess.run(
        ["git", "for-each-ref", "refs/review-pr"], cwd=repo, capture_output=True, text=True, check=True,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    ).stdout.strip()


@pytest.fixture
def isolated_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    return home


@pytest.fixture
def repo_with_pr_ref(tmp_path):
    return _build_repo_with_pr_ref(tmp_path)


# Sentinel default for head_repo_full_name/base_repo_full_name below: "same
# repo as whatever the request path actually named" -- computed from the
# shimmed request itself (args[1] = "repos/<owner>/<repo>/pulls/<n>"),
# rather than baked to the OWNER_REPO module constant, so a test using a
# different owner/repo (e.g. TestOwnerRepoRegexAcceptsDotAndHyphenAlongsideAlnum's
# "my-org/my.repo") still gets a safe, same-repo trust response by default.
_SAME_AS_REQUEST = "__SAME_AS_REQUEST__"

_ALLOWLISTED_ASSOCIATIONS = ["MEMBER", "OWNER", "COLLABORATOR", "CONTRIBUTOR"]

# Sentinel default for `_run`'s base_ref_oid: "the commit origin/main points
# at in the repo under test" -- the base commit _build_repo_with_pr_ref's PR
# branched from -- so a test that does not care about the base still reaches
# a successful diff. None omits baseRefOid from the shimmed gh reply.
_BASE_FROM_ORIGIN_MAIN = "__BASE_FROM_ORIGIN_MAIN__"


def _is_listing_call(call: list[str], suffix: str) -> bool:
    """True for a `gh api` call to the pulls listing ending in `suffix`,
    whatever query string it carries."""
    return call[:1] == ["api"] and call[1].split("?", 1)[0].endswith(suffix)


def _gh_shim_source(
    call_log: Path,
    head_ref_oid: str | None = None,
    files: list[str] | None = None,
    fail_pr_view: bool = False,
    fail_files: bool = False,
    partial_files_then_fail: list[str] | None = None,
    head_ref_oid_second: str | None = None,
    author_association: str | None = "MEMBER",
    head_repo_full_name: str | None = _SAME_AS_REQUEST,
    base_repo_full_name: str | None = _SAME_AS_REQUEST,
    fail_trust_check: bool = False,
    malformed_trust_check: bool = False,
    changed_files: int | str | None = None,
    omit_changed_files: bool = False,
    files_failure_exit_status: int = 1,
    base_ref_oid: str | None = None,
    base_ref_name: str | None = "main",
    base_default_branch: str | None = "main",
) -> str:
    """gh shim recording every invocation, dispatching on the invocation's own
    first word(s): `gh pr view ... --json headRefOid` returns `head_ref_oid`
    (`head_ref_oid_second` from the SECOND call onward, modeling a force-push
    between the initial fetch and the pre-audit re-fetch); a `gh pr view`
    carrying no `--jq` prints the JSON object `{"headRefOid", "baseRefOid"}`
    instead, with `baseRefOid` omitted when `base_ref_oid` is None; `gh api
    .../pulls/N` returns the trust-classification payload; `gh api
    .../files --paginate ...` prints `files`.
    `head_repo_full_name`/`base_repo_full_name` default to
    `_SAME_AS_REQUEST`, echoing the request path's own owner/repo, so a test
    that doesn't care about trust classification reaches checkout for any
    owner/repo. `head_repo_full_name=None` models a deleted-fork PR (REST
    `head.repo` null); `author_association=None` models a null value.
    `fail_*` flags exit 1 on the matching call only.
    `partial_files_then_fail` prints those filenames, then exits 1, modeling
    `--paginate` failing after emitting a page. `malformed_trust_check` exits 0
    but prints non-JSON. `fail_files` and `partial_files_then_fail` exit
    `files_failure_exit_status`.
    `changed_files` is the REST payload's own count, independent of the
    `files` listing: it defaults to `len(files)`, a test sets it apart from
    `files` to model a truncated or padded listing, and `omit_changed_files`
    drops the field.
    `base_ref_name` and `base_default_branch` are the REST payload's `base.ref`
    and `base.repo.default_branch`; a value of None drops that field.
    The files listing prints one JSON string per line when the call carries
    the `@json` jq filter, and raw names otherwise: raw UTF-8, with a control
    character rendered in caret notation as gh 2.100.0 is modeled to do (see
    the shim constant in conftest.py). The listing call must carry
    `--paginate` and `per_page=100`; the shim exits 97 otherwise."""
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json
        import os
        import sys

        CALL_LOG = {str(call_log)!r}
        HEAD_REF_OID = {head_ref_oid!r}
        HEAD_REF_OID_SECOND = {head_ref_oid_second!r}
        FILES = {list(files or [])!r}
        FAIL_PR_VIEW = {fail_pr_view!r}
        FAIL_FILES = {fail_files!r}
        PARTIAL_FILES_THEN_FAIL = {list(partial_files_then_fail or [])!r}
        AUTHOR_ASSOCIATION = {author_association!r}
        HEAD_REPO_FULL_NAME = {head_repo_full_name!r}
        BASE_REPO_FULL_NAME = {base_repo_full_name!r}
        SAME_AS_REQUEST = {_SAME_AS_REQUEST!r}
        FAIL_TRUST_CHECK = {fail_trust_check!r}
        MALFORMED_TRUST_CHECK = {malformed_trust_check!r}
        CHANGED_FILES = {len(files or []) if changed_files is None else changed_files!r}
        OMIT_CHANGED_FILES = {omit_changed_files!r}
        FILES_FAILURE_EXIT_STATUS = {files_failure_exit_status!r}
        BASE_REF_OID = {base_ref_oid!r}
        BASE_REF_NAME = {base_ref_name!r}
        BASE_DEFAULT_BRANCH = {base_default_branch!r}
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
        record = {{
            "args": args,
        }}
        with open(CALL_LOG, "a") as f:
            f.write(json.dumps(record) + chr(10))
        if args[:2] == ["pr", "view"]:
            if FAIL_PR_VIEW:
                sys.exit(1)
            oid = HEAD_REF_OID
            if HEAD_REF_OID_SECOND is not None and prior_pr_view_calls >= 1:
                oid = HEAD_REF_OID_SECOND
            if "--jq" in args:
                if oid:
                    print(oid)
                sys.exit(0)
            reply = {{}}
            if oid:
                reply["headRefOid"] = oid
            if BASE_REF_OID is not None:
                reply["baseRefOid"] = BASE_REF_OID
            print(json.dumps(reply))
            sys.exit(0)
        if args[:1] == ["api"] and len(args) >= 2 and not args[1].split("?")[0].endswith("/files"):
            if FAIL_TRUST_CHECK:
                sys.exit(1)
            if MALFORMED_TRUST_CHECK:
                print("not json")
                sys.exit(0)
            path_parts = args[1].split("/")
            requested_repo = path_parts[1] + "/" + path_parts[2] if len(path_parts) >= 3 else None
            head_name = requested_repo if HEAD_REPO_FULL_NAME == SAME_AS_REQUEST else HEAD_REPO_FULL_NAME
            base_name = requested_repo if BASE_REPO_FULL_NAME == SAME_AS_REQUEST else BASE_REPO_FULL_NAME
            head_repo = {{"full_name": head_name}} if head_name is not None else None
            base_repo = {{"full_name": base_name}}
            if BASE_DEFAULT_BRANCH is not None:
                base_repo["default_branch"] = BASE_DEFAULT_BRANCH
            base = {{"repo": base_repo}}
            if BASE_REF_NAME is not None:
                base["ref"] = BASE_REF_NAME
            payload = {{
                "author_association": AUTHOR_ASSOCIATION,
                "head": {{"repo": head_repo}},
                "base": base,
            }}
            if not OMIT_CHANGED_FILES:
                payload["changed_files"] = CHANGED_FILES
            print(json.dumps(payload))
            sys.exit(0)
        if args[:1] == ["api"]:
            if "--paginate" not in args or not args[1].endswith("?per_page=100"):
                print("shim: listing call without --paginate and per_page=100", file=sys.stderr)
                sys.exit(97)
            encode = (
                (lambda name: json.dumps(name, ensure_ascii=False))
                if ".[].filename | @json" in args else str
            )
            emit = lambda name: sys.stdout.buffer.write((encode(sanitize_like_gh(name)) + chr(10)).encode("utf-8"))
            if PARTIAL_FILES_THEN_FAIL:
                for f in PARTIAL_FILES_THEN_FAIL:
                    emit(f)
                sys.exit(FILES_FAILURE_EXIT_STATUS)
            if FAIL_FILES:
                sys.exit(FILES_FAILURE_EXIT_STATUS)
            for f in FILES:
                emit(f)
            sys.exit(0)
        sys.exit(0)
    """)


def _read_calls(call_log: Path) -> list[list[str]]:
    if not call_log.exists():
        return []
    return [json.loads(line)["args"] for line in call_log.read_text().splitlines() if line]


def _read_records(call_log: Path) -> list[dict]:
    if not call_log.exists():
        return []
    return [json.loads(line) for line in call_log.read_text().splitlines() if line]


def _run(
    cwd: Path,
    home: Path,
    args: list[str],
    tmp_path: Path,
    *,
    head_ref_oid: str | None = None,
    files: list[str] | None = None,
    fail_pr_view: bool = False,
    fail_files: bool = False,
    partial_files_then_fail: list[str] | None = None,
    head_ref_oid_second: str | None = None,
    author_association: str | None = "MEMBER",
    head_repo_full_name: str | None = _SAME_AS_REQUEST,
    base_repo_full_name: str | None = _SAME_AS_REQUEST,
    fail_trust_check: bool = False,
    malformed_trust_check: bool = False,
    changed_files: int | str | None = None,
    omit_changed_files: bool = False,
    files_failure_exit_status: int = 1,
    base_ref_oid: str | None = _BASE_FROM_ORIGIN_MAIN,
    base_ref_name: str | None = "main",
    base_default_branch: str | None = "main",
    extra_env: dict | None = None,
    path_prefix: Path | None = None,
) -> tuple[subprocess.CompletedProcess, Path]:
    # A successful checkout writes provenance, which needs a live session
    # -- seeded unconditionally, harmlessly idempotent for the many tests
    # here that abort before ever reaching that write.
    _seed_session(home, SID)
    if base_ref_oid == _BASE_FROM_ORIGIN_MAIN:
        base_ref_oid = _origin_main_sha(cwd)
    call_log = tmp_path / "gh_calls.jsonl"
    env = {
        **_shimmed_env(
            tmp_path,
            _gh_shim_source(
                call_log, head_ref_oid, files, fail_pr_view, fail_files, partial_files_then_fail,
                head_ref_oid_second, author_association, head_repo_full_name, base_repo_full_name,
                fail_trust_check, malformed_trust_check, changed_files, omit_changed_files,
                files_failure_exit_status, base_ref_oid, base_ref_name, base_default_branch,
            ),
        ),
        "HOME": str(home),
        # Same isolation as _independent_git_diff, so the script's config-dependent
        # output matches the oracle. GIT_CONFIG_GLOBAL needs git 2.32 or later.
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    env.pop("CLAUDE_CONFIG_DIR", None)
    if path_prefix is not None:
        env["PATH"] = f"{path_prefix}{os.pathsep}{env['PATH']}"
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    )
    _assert_gh_calls_are_read_only(_read_calls(call_log), known_api_endpoint=_KNOWN_API_ENDPOINT)
    return result, call_log


class TestUsageErrors:
    @pytest.mark.parametrize(
        "args",
        [
            [],
            ["foo/bar#42", "extra"],
            ["no-hash-or-slash"],
            ["foo/bar#NOTANUMBER"],
            ["#42"],
            # A segment made entirely of '.'/'-' characters is a valid,
            # nonempty run under a naive [A-Za-z0-9._-]+ class, and turns a
            # `repos/$OWNER_REPO/...` gh api interpolation into a
            # path-traversal shape -- these must be rejected by the owner/
            # repo regex before any git/gh call, the same as the other
            # malformed-argv shapes above.
            ["../..#5"],
            ["..#5"],
        ],
    )
    def test_invalid_argv_exits_two_with_no_gh_call(self, isolated_home, repo_with_pr_ref, tmp_path, args):
        repo, _ = repo_with_pr_ref
        result, call_log = _run(repo, isolated_home, args, tmp_path)
        assert result.returncode == 2
        assert _read_calls(call_log) == []


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
        assert _review_worktrees(repo) == [], "finish removes the review worktree the checkout created"


class TestOwnerRepoRegexAcceptsDotAndHyphenAlongsideAlnum:
    def test_owner_repo_with_dot_and_hyphen_segments_passes_regex_and_checks_out(
        self, isolated_home, tmp_path
    ):
        """Bounds the owner/repo regex from the other side of
        TestUsageErrors' dot-only-segment deny cases: a segment mixing '.'/
        '-' with at least one alphanumeric character (an ordinary GitHub
        owner/repo shape) must still pass, and the whole run must still
        reach a successful checkout -- not just clear the regex check."""
        owner_repo = "my-org/my.repo"
        repo, pr_sha = _build_repo_with_pr_ref(tmp_path, owner_repo=owner_repo)
        _install_audit_script(isolated_home)
        result, call_log = _run(
            repo, isolated_home, [f"{owner_repo}#{PR_NUMBER}"], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 0, result.stderr
        assert _read_calls(call_log) != []


class TestOwnerRepoOriginMismatch:
    def test_owner_repo_differing_from_origin_aborts_before_any_fetch(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """The cross-repo audit-substitution bypass this check closes: a
        PR's headRefOid is content-addressed, so an attacker can push the
        real PR's own head commit to a second, attacker-controlled repo
        against a decoy base and induce this script to be invoked with that
        decoy's OWNER_REPO instead of the real PR's -- the decoy's reported
        headRefOid can be made to equal the real one, so the fetched-SHA-vs-
        headRefOid check further down in the script can't catch it on its
        own. This check must fire first, before headRefOid is even
        fetched -- repo_with_pr_ref's own origin resolves to `foo/bar`
        (OWNER_REPO), so a PR identity naming a different repo must be
        rejected here."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [f"decoy-owner/decoy-repo#{PR_NUMBER}"], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 2, result.stderr
        assert "origin" in result.stderr
        assert _read_calls(call_log) == []
        assert _local_pr_ref_names(repo) == ""
        assert _review_worktrees(repo) == []


class TestOwnerRepoCaseInsensitiveMatch:
    def test_pr_identity_case_differing_from_origin_still_checks_out(
        self, isolated_home, tmp_path
    ):
        """GitHub treats owner/repo slugs case-insensitively, so a PR
        identity spelled with different case than origin's own stored URL
        case must still match, not be misread as a cross-repo target and
        refused by TestOwnerRepoOriginMismatch's own check above."""
        origin_owner_repo = "Foo-Org/Bar-Repo"
        repo, pr_sha = _build_repo_with_pr_ref(tmp_path, owner_repo=origin_owner_repo)
        _install_audit_script(isolated_home)
        result, call_log = _run(
            repo, isolated_home, [f"foo-org/bar-repo#{PR_NUMBER}"], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 0, result.stderr
        worktree_dir = _printed_worktree_path(result)
        assert worktree_dir.exists()
        checked_out_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=worktree_dir, capture_output=True, text=True, check=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        ).stdout.strip()
        assert checked_out_head == pr_sha


class TestMissingAuditScript:
    def test_uninstalled_skill_directory_aborts_before_any_fetch(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """No `_install_audit_script` call here -- the isolated $HOME has no
        review-pr skill installed at all, the state a config dir missing the
        claude-skills/ stow package is actually in."""
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"]
        )
        assert result.returncode == 2, result.stderr
        assert "audit script not found" in result.stderr
        assert _read_calls(call_log) == []


class TestTrustClassificationRefuses:
    """Script exit-code tests with a PATH-shimmed `gh`, not hook-deny tests
    -- the trust block lives in the script by design (see
    review-pr-checkout.sh's header). Trust classification widens the stop
    conditions; it never removes one."""

    # GitHub's eight defined author_association values are OWNER, MEMBER,
    # COLLABORATOR, CONTRIBUTOR (checked out below), and the four refused
    # here; "SOMETHING_NEW" stands in for a value GitHub adds later, which an
    # allowlist must refuse by default.
    @pytest.mark.parametrize(
        "author_association",
        ["FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", "MANNEQUIN", "NONE", "SOMETHING_NEW"],
    )
    def test_restricted_author_association_refuses_with_no_fetch_or_worktree(
        self, isolated_home, repo_with_pr_ref, tmp_path, author_association
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], author_association=author_association,
        )
        assert result.returncode == 3, "a trust refusal exits 3, distinct from exit 2's operational failures"
        assert "review-pr-diff.sh" in result.stderr
        assert _local_pr_ref_names(repo) == ""
        assert _review_worktrees(repo) == []
        # No files pagination either -- the block fires before it.
        assert not any(_is_listing_call(c, "/files") for c in _read_calls(call_log))

    @pytest.mark.parametrize(
        "author_association,expected_exit_status",
        [(None, 2), ("", 2), ("member", 3)],
        ids=["null", "empty", "lowercase"],
    )
    def test_unusable_or_misspelled_author_association_refuses_with_no_fetch_or_worktree(
        self, isolated_home, repo_with_pr_ref, tmp_path, author_association, expected_exit_status
    ):
        """A null or empty value is a malformed response (exit 2); a lowercase
        spelling is not in the allowlist, which matches exact GitHub values
        (exit 3). Neither reaches a fetch."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], author_association=author_association,
        )
        assert result.returncode == expected_exit_status
        assert _local_pr_ref_names(repo) == ""
        assert _review_worktrees(repo) == []
        assert not any(_is_listing_call(c, "/files") for c in _read_calls(call_log))

    def test_cross_repo_via_differing_full_name_refuses(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"],
            head_repo_full_name="some-fork/bar", base_repo_full_name=OWNER_REPO,
        )
        assert result.returncode == 3
        assert "review-pr-diff.sh" in result.stderr
        assert _local_pr_ref_names(repo) == ""
        assert _review_worktrees(repo) == []

    @pytest.mark.parametrize("author_association", _ALLOWLISTED_ASSOCIATIONS)
    def test_deleted_fork_null_head_repo_refuses(
        self, isolated_home, repo_with_pr_ref, tmp_path, author_association
    ):
        """A null `head.repo` (REST payload) means the PR's fork was
        deleted -- treated as cross-repo, the more restrictive read on an
        ambiguous input, never as a pass, whatever the author's standing."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], author_association=author_association,
            head_repo_full_name=None,
        )
        assert result.returncode == 3
        assert "review-pr-diff.sh" in result.stderr
        assert _review_worktrees(repo) == []

    @pytest.mark.parametrize("author_association", _ALLOWLISTED_ASSOCIATIONS)
    def test_allowlisted_author_paired_with_cross_repo_still_refuses(
        self, isolated_home, repo_with_pr_ref, tmp_path, author_association
    ):
        """No allowlisted author association may itself waive the cross-repo
        check -- otherwise a suite that only checks cross-repo status for
        non-members would pass, which is the exact standing-gated shape this
        design rejects."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], author_association=author_association,
            head_repo_full_name="some-fork/bar", base_repo_full_name=OWNER_REPO,
        )
        assert result.returncode == 3
        assert "review-pr-diff.sh" in result.stderr
        assert _review_worktrees(repo) == []

    @pytest.mark.parametrize("author_association", _ALLOWLISTED_ASSOCIATIONS)
    def test_allowlisted_author_same_repo_still_checks_out(
        self, isolated_home, repo_with_pr_ref, tmp_path, author_association
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], author_association=author_association,
        )
        assert result.returncode == 0, result.stderr
        assert [_printed_worktree_path(result)] == _review_worktrees(repo)

    def test_pr_into_the_default_branch_proceeds_to_the_ref_fetch(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"],
            base_ref_name="trunk", base_default_branch="trunk",
        )
        assert result.returncode == 0, result.stderr
        assert _local_pr_ref_names(repo) != ""

    def test_pr_into_a_non_default_base_refuses_before_any_fetch(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """The audit sees only the PR's changed files, so a stacked PR whose
        base already holds an execution-surface file passes the audit clean
        while the checkout writes that file. The script never sees base
        content, so the refusal keys on the base branch alone."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"],
            base_ref_name="feature-base", base_default_branch="main",
        )
        assert result.returncode == 3
        assert "review-pr-diff.sh" in result.stderr
        assert "feature-base" in result.stderr
        assert not any(_is_listing_call(c, "/files") for c in _read_calls(call_log))
        assert _local_pr_ref_names(repo) == ""
        assert _review_worktrees(repo) == []

    @pytest.mark.parametrize(
        "base_ref_name",
        ["Main", "refs/heads/main", " main", "main "],
        ids=["case-variant", "refs-heads-prefix", "leading-space", "trailing-space"],
    )
    def test_base_branch_comparison_is_exact_not_normalized(
        self, isolated_home, repo_with_pr_ref, tmp_path, base_ref_name
    ):
        """Git ref names are case-sensitive, so a base that differs from the
        default branch only by case, prefix, or whitespace is a different
        branch and is refused."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"],
            base_ref_name=base_ref_name, base_default_branch="main",
        )
        assert result.returncode == 3, result.stderr
        assert "review-pr-diff.sh" in result.stderr
        assert not any(_is_listing_call(c, "/files") for c in _read_calls(call_log))
        assert _local_pr_ref_names(repo) == ""
        assert _review_worktrees(repo) == []

    @pytest.mark.parametrize(
        "missing_field", ["base_ref_name", "base_default_branch"],
    )
    def test_absent_base_branch_field_exits_two_never_three(
        self, isolated_home, repo_with_pr_ref, tmp_path, missing_field
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], **{missing_field: None},
        )
        assert result.returncode == 2, result.stderr
        assert "undecided" in result.stderr
        assert "review-pr-diff.sh" not in result.stderr
        assert _local_pr_ref_names(repo) == ""
        assert _review_worktrees(repo) == []

    def test_trust_block_fires_before_the_paginated_file_list_call(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], author_association="NONE",
        )
        assert result.returncode == 3, result.stderr
        calls = _read_calls(call_log)
        assert calls, "the trust-check call itself must still have been made"
        assert calls[0][:1] == ["api"] and not _is_listing_call(calls[0], "/files"), (
            "the trust-classification call must be the first gh invocation, "
            "strictly before the paginated files listing"
        )
        assert not any(_is_listing_call(c, "/files") for c in calls)

    def test_trust_check_gh_failure_aborts_rather_than_falling_through_to_checkout(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """A `gh` failure on the trust-check call itself must never be read
        as 'no restriction found' -- the same "any gh failure aborts"
        discipline SKILL.md Step 1 states, extended to this call."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], fail_trust_check=True,
        )
        assert result.returncode == 2, "an operational failure is not a trust refusal"
        assert _review_worktrees(repo) == []

    def test_trust_check_malformed_response_aborts(self, isolated_home, repo_with_pr_ref, tmp_path):
        """A malformed (non-JSON) trust-check response is distinct from an
        outright `gh` failure -- both must abort, never fall through."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], malformed_trust_check=True,
        )
        assert result.returncode == 2, "an operational failure is not a trust refusal"
        assert _review_worktrees(repo) == []


class TestProvenanceWrite:
    """review-pr-checkout.sh rewrites this session's provenance file with
    mode "checkout" after its own independent re-derivation -- never
    trusting review-pr-acquire.sh's own mode "acquired" write."""

    def test_successful_checkout_writes_checkout_mode_provenance(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 0, result.stderr
        provenance = isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.provenance"
        assert provenance.exists()
        fields = _provenance_fields(provenance)
        assert fields["pr_identity"] == PR_IDENTITY
        assert fields["head_ref_oid"] == pr_sha
        assert fields["pid"].isdigit()
        assert fields["mode"] == "checkout"

    def test_successful_checkout_then_marker_write_pins_mode_field_in_both_files(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """Pins the shared "mode" field across the two artifacts
        review-pr-checkout.sh and marker.sh's `write review-pr` arm
        produce: the provenance file's key=value schema is read by key
        (_lib_review_pr_provenance_field), immune to a future field being
        added anywhere in the file, while the completion marker is a 4-line
        positional schema (mode is its LAST field, not
        merely index 3) -- a future field inserted there must not silently
        break _lib_review_pr_completion_marker_fields's sed -n '4p' / this test's lines[-1] read."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 0, result.stderr
        worktree_dir = _printed_worktree_path(result)

        provenance = isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.provenance"
        assert _provenance_fields(provenance)["mode"] == "checkout"

        findings_body = isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.body"
        findings_body.write_text(f"**[Claude Code]** # findings\n\n{_ATTRIBUTION_TRAILER}\n")

        marker_env = {"HOME": str(isolated_home)}
        marker_env.pop("CLAUDE_CONFIG_DIR", None)
        marker_result = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "marker.sh"), "write", "review-pr"],
            cwd=worktree_dir, env=marker_env, capture_output=True, text=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
        assert marker_result.returncode == 0, marker_result.stderr
        marker_dir = isolated_home / ".claude" / "review-pr-markers"
        marker_path = next(marker_dir.iterdir())
        assert marker_path.read_text().splitlines()[-1] == "checkout"

    def test_refused_trust_class_writes_no_provenance(self, isolated_home, repo_with_pr_ref, tmp_path):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], author_association="NONE",
        )
        assert result.returncode == 3, result.stderr
        provenance = isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.provenance"
        assert not provenance.exists()


def _push_new_base_commit(repo: Path, tmp_path: Path) -> str:
    """Pushes a commit adding base_only.txt to origin's main from a separate
    clone, so the commit is absent from `repo`'s object store, and returns
    its SHA. The PR branched before this commit, so a three-dot diff against
    it must leave base_only.txt out."""
    origin_url = subprocess.run(
        ["git", "remote", "get-url", "origin"], cwd=repo, capture_output=True, text=True, check=True,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    ).stdout.strip()
    clone = tmp_path / "base-clone"
    for command in (
        ["git", "clone", "-q", "-b", "main", origin_url, str(clone)],
        ["git", "-C", str(clone), "config", "user.email", "test@test.com"],
        ["git", "-C", str(clone), "config", "user.name", "test"],
    ):
        subprocess.run(command, check=True, capture_output=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS)
    (clone / "base_only.txt").write_text("base moved\n")
    for command in (
        ["git", "-C", str(clone), "add", "base_only.txt"],
        ["git", "-C", str(clone), "commit", "-q", "-m", "base moved"],
        ["git", "-C", str(clone), "push", "-q", "origin", "main"],
    ):
        subprocess.run(command, check=True, capture_output=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS)
    return subprocess.run(
        ["git", "-C", str(clone), "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    ).stdout.strip()


class TestReviewDiffFile:
    """review-pr-checkout.sh writes the PR's three-dot diff to the session's
    .diff artifact and prints that path after the worktree path, so the review
    reads the diff from a file rather than a size-capped Bash result."""

    def test_diff_file_holds_the_pr_changes_against_the_fetched_base(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        moved_base_sha = _push_new_base_commit(repo, tmp_path)
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["pr_file.txt"], base_ref_oid=moved_base_sha,
        )
        assert result.returncode == 0, result.stderr
        assert len(result.stdout.splitlines()) == 2, "worktree path, then diff path"

        diff_file = _printed_diff_path(result)
        assert diff_file == isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.diff"
        diff_text = diff_file.read_text()
        assert "+pr change" in diff_text
        assert "base_only.txt" not in diff_text, "a three-dot diff leaves out what the base gained since the merge base"

        subprocess.run(
            ["git", "cat-file", "-e", f"{moved_base_sha}^{{commit}}"], cwd=repo, check=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
        first_pr_view = next(c for c in _read_calls(call_log) if c[:2] == ["pr", "view"])
        assert "headRefOid,baseRefOid" in first_pr_view

    @pytest.mark.parametrize(
        "base_ref_oid",
        [
            None,
            "",
            "main",
            "abc123",
            "--upload-pack=option-shaped",
            "G" * 40,
            "a" * 41,
            "a" * 63,
            "a" * 65,
            "A" * 40,
            "a" * 40 + "\n--upload-pack=option-shaped",
        ],
        ids=[
            "omitted", "empty", "branch-name", "short-hex", "option-shaped", "non-hex",
            "41-hex", "63-hex", "65-hex", "uppercase-hex", "40-hex-then-option-line",
        ],
    )
    def test_base_ref_oid_that_is_not_a_full_hex_object_name_aborts_before_any_fetch(
        self, isolated_home, repo_with_pr_ref, tmp_path, base_ref_oid
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], base_ref_oid=base_ref_oid,
        )
        assert result.returncode == 2, result.stderr
        assert "baseRefOid" in result.stderr
        assert result.stdout == ""
        assert _local_pr_ref_names(repo) == ""
        assert _review_worktrees(repo) == []

    def test_a_64_hex_base_ref_oid_passes_validation_and_fails_at_the_fetch(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """A SHA-256 object name is a valid shape. The fetch is what fails for
        it here, so the message must be the fetch failure, not the
        validation one."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], base_ref_oid="a" * 64,
        )
        assert result.returncode == 2, result.stderr
        assert "could not fetch baseRefOid" in result.stderr
        assert "not a full hex object name" not in result.stderr

    def test_base_commit_the_remote_does_not_have_aborts_without_a_worktree_or_diff_file(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], base_ref_oid="1" * 40,
        )
        assert result.returncode == 2, result.stderr
        assert "could not fetch baseRefOid" in result.stderr
        assert result.stdout == ""
        assert _review_worktrees(repo) == []
        assert not (isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.diff").exists()


    def test_diff_file_matches_an_independent_diff_for_a_pr_file_over_30_kb(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """The harness truncates a Bash result over about 30 KB, which is why
        the diff goes to a file; a truncating write would pass a substring
        check, so compare every byte against git's own output."""
        _install_audit_script(isolated_home)
        repo, _ = repo_with_pr_ref
        base_sha = _origin_main_sha(repo)
        (repo / "big.txt").write_text("".join(f"line {line_number}\n" for line_number in range(6000)))
        subprocess.run(["git", "add", "big.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "big pr commit"], cwd=repo, check=True)
        big_pr_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        ).stdout.strip()
        subprocess.run(["git", "push", "-q", "origin", f"+HEAD:refs/pull/{PR_NUMBER}/head"], cwd=repo, check=True)
        subprocess.run(["git", "reset", "-q", "--hard", base_sha], cwd=repo, check=True)

        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=big_pr_sha, files=["pr_file.txt", "big.txt"],
        )
        assert result.returncode == 0, result.stderr

        expected_diff = _independent_git_diff(repo, base_sha, big_pr_sha, isolated_home).stdout
        assert len(expected_diff) > 30 * 1024, "the PR must be large enough to exceed the harness's Bash result cap"
        assert _printed_diff_path(result).read_bytes() == expected_diff

    @pytest.mark.parametrize(
        ("trace_variable", "trace_target", "trace_marker"),
        [
            ("GIT_TRACE", "1", "trace:"),
            ("GIT_TRACE", "/dev/stdout", "trace:"),
            ("GIT_TRACE2", "/dev/stdout", "cmd_name"),
        ],
        ids=["trace-to-stderr", "trace-to-stdout", "trace2-to-stdout"],
    )
    def test_git_trace_output_reaches_neither_the_diff_file_nor_the_printed_paths(
        self, isolated_home, repo_with_pr_ref, tmp_path, trace_variable, trace_target, trace_marker
    ):
        """A trace target of stdout would put trace lines into the captured
        diff text, and so into the diff file the review reads."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        base_sha = _origin_main_sha(repo)
        trace_env = {trace_variable: trace_target}
        expected_diff = _independent_git_diff(repo, base_sha, pr_sha, isolated_home).stdout
        control = _independent_git_diff(repo, base_sha, pr_sha, isolated_home, extra_env=trace_env)
        assert trace_marker.encode() in control.stdout + control.stderr, (
            "control: the variable makes git print trace output"
        )

        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["pr_file.txt"], extra_env=trace_env,
        )

        assert result.returncode == 0, result.stderr
        assert len(result.stdout.splitlines()) == 2, "worktree path, then diff path"
        assert trace_marker not in result.stdout + result.stderr
        assert _printed_diff_path(result).read_bytes() == expected_diff

    def test_a_warning_git_writes_to_stderr_during_a_successful_diff_stays_out_of_the_diff_file(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """A `warning:` line on stderr, such as a rename-limit notice, would
        otherwise read as diff text and stop the empty-diff guard from firing."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        base_sha = _origin_main_sha(repo)
        shim_dir = _git_shim_that_warns_on_stderr_during_diff(tmp_path)

        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["pr_file.txt"], path_prefix=shim_dir,
        )

        assert result.returncode == 0, result.stderr
        assert (shim_dir / "warned.log").exists(), "control: the shim wrote its warning during the diff"
        diff_bytes = _printed_diff_path(result).read_bytes()
        assert b"warning:" not in diff_bytes
        assert diff_bytes == _independent_git_diff(repo, base_sha, pr_sha, isolated_home).stdout

    def test_git_diff_opts_in_the_environment_does_not_change_the_diff_files_context(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """GIT_DIFF_OPTS=-u<N> overrides --unified, so without the script unsetting
        it a contributor's shell could shrink the reviewed context to nothing."""
        _install_audit_script(isolated_home)
        repo, _ = repo_with_pr_ref
        base_sha, head_sha = _push_pr_head_commit(repo, {"file.txt": "above\nmain\nbelow\n"})
        zero_context_env = {"GIT_DIFF_OPTS": "-u0"}
        expected_diff = _independent_git_diff(repo, base_sha, head_sha, isolated_home).stdout
        control = _independent_git_diff(repo, base_sha, head_sha, isolated_home, extra_env=zero_context_env)
        assert control.stdout != expected_diff, "control: GIT_DIFF_OPTS=-u0 changes a plain git diff's context"

        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=head_sha, files=["file.txt"], extra_env=zero_context_env,
        )

        assert result.returncode == 0, result.stderr
        assert _printed_diff_path(result).read_bytes() == expected_diff

    def test_a_dash_diff_gitattributes_added_by_the_pr_head_does_not_hide_that_files_hunks(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """The diff runs in the main tree, whose attributes the PR's own
        .gitattributes has not reached, so a `-diff` rule the PR adds for one of
        its changed files must not turn that file into "Binary files ... differ".
        The audit refuses any listed .gitattributes path, so the file list given
        here omits it: this pins that the diff step does not depend on that refusal."""
        _install_audit_script(isolated_home)
        repo, _ = repo_with_pr_ref
        base_sha, head_sha = _push_pr_head_commit(
            repo, {".gitattributes": "pr_file.txt -diff\n", "pr_file.txt": "pr change\n"}
        )

        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=head_sha, files=["pr_file.txt"],
        )

        assert result.returncode == 0, result.stderr
        diff_bytes = _printed_diff_path(result).read_bytes()
        assert b"+pr change" in diff_bytes
        assert b"+++ b/.gitattributes" in diff_bytes, "the PR head's .gitattributes is part of the diffed range"
        assert b"Binary files" not in diff_bytes
        assert diff_bytes == _independent_git_diff(repo, base_sha, head_sha, isolated_home).stdout

        # Control: the same range diffed inside a tree that holds the PR's
        # .gitattributes does hide the hunks, so the match above is the main
        # tree's attributes and not a no-op rule.
        control_tree = tmp_path / "control-tree"
        subprocess.run(
            ["git", "worktree", "add", "-q", "--detach", str(control_tree), head_sha], cwd=repo, check=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
        control_diff = _independent_git_diff(control_tree, base_sha, head_sha, isolated_home).stdout
        assert b"Binary files" in control_diff, "control: the PR's -diff rule is live where its tree is checked out"

    @pytest.mark.parametrize(
        "hostile_diff_config",
        [
            "[diff]\n\tignoreSubmodules = all\n\tnoprefix = true\n\tcontext = 0\n",
            "[diff]\n\tignoreSubmodules = all\n\tmnemonicPrefix = true\n\tcontext = 0\n",
            "[diff]\n\tignoreSubmodules = all\n[color]\n\tui = always\n",
        ],
        ids=["noprefix", "mnemonic-prefix", "color-always"],
    )
    def test_diff_file_ignores_operator_diff_config_that_changes_what_is_reviewed(
        self, isolated_home, repo_with_pr_ref, tmp_path, hostile_diff_config
    ):
        """A submodule pointer change must not be dropped, and the prefixes,
        context size, and color escapes must not vary with the operator's
        global git settings."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        base_sha = _origin_main_sha(repo)
        (repo / "file.txt").write_text("above\nmain\nbelow\n")
        subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
        subprocess.run(
            ["git", "update-index", "--add", "--cacheinfo", f"160000,{pr_sha},vendored"], cwd=repo, check=True,
        )
        subprocess.run(["git", "commit", "-q", "-m", "pr with a submodule bump"], cwd=repo, check=True)
        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        ).stdout.strip()
        subprocess.run(["git", "push", "-q", "origin", f"+HEAD:refs/pull/{PR_NUMBER}/head"], cwd=repo, check=True)
        subprocess.run(["git", "reset", "-q", "--hard", base_sha], cwd=repo, check=True)
        hostile_config_path = isolated_home / ".gitconfig"
        hostile_config_path.write_text(hostile_diff_config)
        no_other_config = {"GIT_CONFIG_GLOBAL": str(hostile_config_path), "GIT_CONFIG_NOSYSTEM": "1"}

        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=head_sha, files=["file.txt", "vendored"], extra_env=no_other_config,
        )
        assert result.returncode == 0, result.stderr

        canonical_diff = _independent_git_diff(repo, base_sha, head_sha, isolated_home).stdout
        assert b"Subproject commit" in canonical_diff, "the oracle must include the submodule pointer hunk"
        assert _printed_diff_path(result).read_bytes() == canonical_diff

        # Control: the same settings do change a plain `git diff`, so the match
        # above is the script's pinning and not a no-op configuration.
        hostile_plain_diff = subprocess.run(
            ["git", "diff", f"{base_sha}...{head_sha}", "--"],
            cwd=repo, env={**os.environ, "HOME": str(isolated_home), **no_other_config},
            capture_output=True, check=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        ).stdout
        assert hostile_plain_diff != canonical_diff, "control: the hostile config is live"
        assert b"Subproject commit" not in hostile_plain_diff, "control: ignoreSubmodules = all hides the pointer hunk"

    @pytest.mark.parametrize("driver", ["external", "textconv"])
    def test_a_configured_diff_driver_does_not_run_over_the_pr_content(
        self, isolated_home, repo_with_pr_ref, tmp_path, driver
    ):
        """--no-ext-diff and --no-textconv keep a repo-configured program from
        running over PR-supplied blob bytes."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        driver_ran_sentinel = tmp_path / "driver-ran"
        driver_script = tmp_path / "diff-driver.sh"
        driver_script.write_text(f"#!/bin/sh\ntouch '{driver_ran_sentinel}'\ncat \"$1\" 2>/dev/null || true\n")
        driver_script.chmod(0o755)
        if driver == "external":
            subprocess.run(["git", "config", "diff.external", str(driver_script)], cwd=repo, check=True)
        else:
            subprocess.run(["git", "config", "diff.sentinel.textconv", str(driver_script)], cwd=repo, check=True)
            (repo / ".gitattributes").write_text("pr_file.txt diff=sentinel\n")
        base_sha = _origin_main_sha(repo)

        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["pr_file.txt"],
        )

        assert result.returncode == 0, result.stderr
        assert not driver_ran_sentinel.exists(), "the checkout's diff must not run a configured diff driver"
        assert "+pr change" in _printed_diff_path(result).read_text()

        # Control: a plain `git diff` over the same range does fire the driver,
        # so its absence above is the flags' doing.
        _independent_git_diff(repo, base_sha, pr_sha, isolated_home)
        assert driver_ran_sentinel.exists(), "control: the driver is wired correctly"

    def test_an_empty_diff_for_a_pr_with_changed_files_aborts_without_a_worktree_or_diff_file(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """A base that already contains the head diffs to nothing, which would
        otherwise reach the review as a PR with no changes."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["pr_file.txt"], base_ref_oid=pr_sha,
        )
        assert result.returncode == 2, result.stderr
        assert "is empty but PR" in result.stderr
        assert "1 changed files" in result.stderr
        assert result.stdout == ""
        assert _review_worktrees(repo) == []
        assert not (isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.diff").exists()

    def test_an_empty_diff_for_a_pr_with_no_changed_files_still_succeeds(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=[], base_ref_oid=pr_sha,
        )
        assert result.returncode == 0, result.stderr
        assert _printed_diff_path(result).read_text().strip() == ""


def _push_unrelated_root_commit(repo: Path) -> str:
    """Pushes a parentless commit to a new branch on origin and returns its
    SHA. The base fetch for it succeeds, while a three-dot diff against any
    commit of the PR's own history has no merge base and fails."""
    empty_tree = subprocess.run(
        ["git", "hash-object", "-t", "tree", "/dev/null"], cwd=repo, capture_output=True, text=True,
        check=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    ).stdout.strip()
    root_sha = subprocess.run(
        ["git", "commit-tree", empty_tree, "-m", "unrelated root"], cwd=repo, capture_output=True, text=True,
        check=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    ).stdout.strip()
    subprocess.run(
        ["git", "push", "-q", "origin", f"{root_sha}:refs/heads/unrelated-root"], cwd=repo, check=True,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    )
    return root_sha


def _git_shim_that_fails_on_subcommand(
    tmp_path: Path, args_glob: str, *, exit_status: int, fail_only_first_match: bool = False
) -> Path:
    """A `git` PATH shim that fails any invocation whose space-joined
    arguments match the bash glob `args_glob` with `exit_status` and a
    synthetic stderr line, and delegates every other invocation to the real
    git binary. With `fail_only_first_match`, only the first matching
    invocation fails and later matches reach the real git.
    Each matching invocation appends one line to `matches.log` in the returned
    directory, so a test can count them."""
    shim_dir = tmp_path / "git_shim_on_subcommand"
    shim_dir.mkdir()
    match_log = shim_dir / "matches.log"
    spaces_escaped_glob = args_glob.replace(" ", "\\ ")
    real_git = shlex.quote(shutil.which("git"))
    git_shim = shim_dir / "git"
    git_shim.write_text(
        f'#!/usr/bin/env bash\nif [[ "$*" == {spaces_escaped_glob} ]]; then\n'
        f'  matched_before=0\n  [[ -s {shlex.quote(str(match_log))} ]] && matched_before=1\n'
        f'  echo "$*" >> {shlex.quote(str(match_log))}\n'
        f'  if [[ {int(fail_only_first_match)} == 1 && $matched_before == 1 ]]; then exec {real_git} "$@"; fi\n'
        f'  echo "synthetic git failure" >&2\n  exit {exit_status}\nfi\n'
        f'exec {real_git} "$@"\n'
    )
    git_shim.chmod(0o755)
    return shim_dir


def _shim_match_count(shim_dir: Path) -> int:
    """How many invocations `_git_shim_that_fails_on_subcommand`'s glob matched."""
    match_log = shim_dir / "matches.log"
    return len(match_log.read_text().splitlines()) if match_log.exists() else 0


def _git_shim_that_points_worktree_add_at_a_missing_ref(tmp_path: Path) -> Path:
    """A `git` PATH shim that runs the real git for every call, except that a
    `worktree add` has its final argument (the commit) replaced with a ref that
    does not exist, so git itself rejects the add."""
    shim_dir = tmp_path / "git_shim_missing_ref_on_worktree_add"
    shim_dir.mkdir()
    git_shim = shim_dir / "git"
    real_git = shlex.quote(shutil.which("git"))
    git_shim.write_text(
        "#!/usr/bin/env bash\n"
        f'if [[ "$*" == *" worktree add "* ]]; then exec {real_git} "${{@:1:$#-1}}" refs/heads/no-such-ref; fi\n'
        f'exec {real_git} "$@"\n'
    )
    git_shim.chmod(0o755)
    return shim_dir


def _git_shim_that_warns_on_stderr_during_diff(tmp_path: Path) -> Path:
    """A `git` PATH shim that runs the real git for every call and, after a
    `diff`, writes a `warning:` line to stderr while keeping git's own exit
    status. Each warning appends one line to `warned.log` in the returned
    directory."""
    shim_dir = tmp_path / "git_shim_warning_on_diff"
    shim_dir.mkdir()
    warned_log = shim_dir / "warned.log"
    real_git = shlex.quote(shutil.which("git"))
    git_shim = shim_dir / "git"
    git_shim.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$*" == *" diff "* ]]; then\n'
        f'  {real_git} "$@"\n  diff_status=$?\n'
        '  echo "warning: inexact rename detection was skipped" >&2\n'
        f"  echo warned >> {shlex.quote(str(warned_log))}\n"
        '  exit "$diff_status"\nfi\n'
        f'exec {real_git} "$@"\n'
    )
    git_shim.chmod(0o755)
    return shim_dir


def _git_shim_that_records_exported_variable_names(tmp_path: Path) -> Path:
    """A `git` PATH shim that runs the real git for every call and first
    writes the invocation's arguments to `<pid>.args` and the names of its
    exported variables to `<pid>.names` in the returned directory."""
    shim_dir = tmp_path / "git_shim_recording_variable_names"
    shim_dir.mkdir()
    record_prefix = shlex.quote(str(shim_dir))
    real_git = shlex.quote(shutil.which("git"))
    git_shim = shim_dir / "git"
    git_shim.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" > {record_prefix}/$$.args\n'
        f"compgen -e > {record_prefix}/$$.names\n"
        f'exec {real_git} "$@"\n'
    )
    git_shim.chmod(0o755)
    return shim_dir


def _recorded_git_invocations(shim_dir: Path) -> list[tuple[str, set[str]]]:
    """Each invocation `_git_shim_that_records_exported_variable_names` saw, as
    its space-joined arguments and the set of its exported variable names."""
    return [
        (args_file.read_text().strip(), set(args_file.with_suffix(".names").read_text().split()))
        for args_file in sorted(shim_dir.glob("*.args"))
    ]


# One failing row per step covers the user-visible message. Which variables
# reach a git child is checked by
# test_no_git_child_inherits_a_trace_or_curl_verbose_variable.
_TRACE_FAILURE_STEPS = ["base_fetch", "diff", "worktree_add"]


class TestReviewDiffStepFailure:
    """A failure of the base fetch or the diff aborts before the worktree
    exists, leaves no diff file, and leaves the acquire step's provenance
    alone."""

    DIFF_FILE_NAME = f"{SID}.diff"

    @pytest.mark.parametrize(
        ("exit_status", "expect_cap_note"), [(1, False), (124, True)], ids=["plain_failure", "cap_kill_status"]
    )
    @pytest.mark.parametrize("step", ["base_fetch", "diff"])
    def test_failure_aborts_with_git_diagnostics_and_a_cap_note_only_for_a_cap_kill_status(
        self, isolated_home, repo_with_pr_ref, tmp_path, step, exit_status, expect_cap_note
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        base_sha = _origin_main_sha(repo)
        provenance = write_review_pr_provenance(
            isolated_home, PR_IDENTITY, pr_sha, 999, mode="acquired", session_id=SID
        )
        args_glob = f"*fetch origin {base_sha}" if step == "base_fetch" else "* diff *"
        shim_dir = _git_shim_that_fails_on_subcommand(tmp_path, args_glob, exit_status=exit_status)
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["pr_file.txt"], path_prefix=shim_dir,
        )

        assert result.returncode == 2, result.stderr
        expected_message = "could not fetch baseRefOid" if step == "base_fetch" else "could not compute the diff"
        if expect_cap_note:
            assert f"exited {exit_status}, consistent with the 30s cap firing" in result.stderr
        else:
            assert "consistent with the" not in result.stderr
            assert expected_message in result.stderr
        if step == "diff" and expect_cap_note:
            assert "synthetic git failure" not in result.stderr, "a cap kill must not rerun git for its diagnostic"
            assert _shim_match_count(shim_dir) == 1
        else:
            assert "synthetic git failure" in result.stderr, "git's own diagnostic must reach the message"
            assert _shim_match_count(shim_dir) == (2 if step == "diff" else 1), (
                "a failed diff reruns once for its diagnostic; a failed base fetch is not rerun"
            )
        assert result.stdout == ""
        assert _review_worktrees(repo) == []
        assert not (isolated_home / ".claude" / ".review-pr-active.d" / self.DIFF_FILE_NAME).exists()
        assert _provenance_fields(provenance)["mode"] == "acquired"

    def test_a_diff_that_fails_once_and_then_succeeds_reports_that_git_printed_no_diagnostic(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        shim_dir = _git_shim_that_fails_on_subcommand(
            tmp_path, "* diff *", exit_status=1, fail_only_first_match=True
        )
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["pr_file.txt"], path_prefix=shim_dir,
        )

        assert result.returncode == 2, result.stderr
        assert "could not compute the diff" in result.stderr
        assert "git printed no diagnostic" in result.stderr
        assert "synthetic git failure" not in result.stderr, "the rerun's stderr is what the message carries"
        assert _shim_match_count(shim_dir) == 2

    @pytest.mark.parametrize("step", _TRACE_FAILURE_STEPS)
    def test_git_trace_output_does_not_reach_the_failure_message(
        self, isolated_home, repo_with_pr_ref, tmp_path, step
    ):
        """Trace output goes to stderr, which every failure message relays, so
        the script runs its git calls with the trace variables unset. Each
        failure is real: no such base commit on origin, a base with no merge
        base against the PR head, and a worktree add that git itself rejects."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        path_prefix = None
        base_ref_oid = _BASE_FROM_ORIGIN_MAIN
        if step == "base_fetch":
            base_ref_oid = "1" * 40
            expected_message = "could not fetch baseRefOid"
        elif step == "diff":
            base_ref_oid = _push_unrelated_root_commit(repo)
            expected_message = "could not compute the diff"
        else:
            path_prefix = _git_shim_that_points_worktree_add_at_a_missing_ref(tmp_path)
            expected_message = "git worktree add failed"
        trace_env = {"GIT_TRACE": "1"}

        control = subprocess.run(
            ["git", "fetch", "origin", "1" * 40], cwd=repo, capture_output=True, text=True,
            env={**os.environ, **trace_env}, timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
        assert "trace:" in control.stderr, "control: GIT_TRACE=1 makes git print trace output to stderr"

        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["pr_file.txt"],
            base_ref_oid=base_ref_oid, extra_env=trace_env, path_prefix=path_prefix,
        )

        assert result.returncode == 2, result.stderr
        assert expected_message in result.stderr
        assert "fatal:" in result.stderr, "git's own diagnostic must still reach the message"
        assert "trace:" not in result.stderr

    def test_no_git_child_inherits_a_trace_or_curl_verbose_variable(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """Names the variables at the process boundary, so the libcurl verbose
        variable is covered although git prints nothing for it against the
        local test origin. An unrelated exported GIT_* variable must still
        reach the children."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        shim_dir = _git_shim_that_records_exported_variable_names(tmp_path)
        trace_variable_names = {
            "GIT_TRACE", "GIT_TRACE2_EVENT", "GIT_TRACE_REDACT", "GIT_CURL_VERBOSE",
        }
        unrelated_variable_name = "GIT_AUTHOR_NAME"
        caller_env = {name: "1" for name in trace_variable_names}
        caller_env[unrelated_variable_name] = "reviewer"

        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["pr_file.txt"], extra_env=caller_env, path_prefix=shim_dir,
        )

        assert result.returncode == 0, result.stderr
        invocations = _recorded_git_invocations(shim_dir)
        base_sha = _origin_main_sha(repo)
        for step_marker in (" fetch origin refs/pull/", f" fetch origin {base_sha} ", " diff ", " worktree add "):
            assert any(step_marker in f" {args} " for args, _ in invocations), f"no git call matched {step_marker!r}"
        for args, variable_names in invocations:
            leaked = {name for name in variable_names if name.startswith(("GIT_TRACE", "GIT_CURL_VERBOSE"))}
            assert not leaked, f"git {args} inherited {sorted(leaked)}"
            assert unrelated_variable_name in variable_names, f"git {args} lost {unrelated_variable_name}"


class TestInvokedFromALinkedWorktree:
    def test_worktree_dir_lands_under_the_main_tree_not_the_linked_worktree(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """review-pr-checkout.sh must create the worktree under the main
        tree even when the session invoking it is standing in a linked
        worktree of the same repo -- review-pr-finish.sh discovers review
        worktrees only under the main tree's .claude/worktrees, so a
        worktree created anywhere else is orphaned on cleanup."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        linked_worktree = tmp_path / "session-worktree"
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(linked_worktree), "HEAD"],
            cwd=repo, check=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )

        result, call_log = _run(
            linked_worktree, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 0, result.stderr

        actual_worktree_dir = _printed_worktree_path(result)
        assert [actual_worktree_dir] == _review_worktrees(repo)
        assert actual_worktree_dir.exists()
        assert not str(actual_worktree_dir).startswith(str(linked_worktree))

    def test_a_pr_supplied_gitattributes_in_a_prior_review_worktree_does_not_change_the_diff(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """git diff reads .gitattributes from the tree it runs in, so a rerun from
        a prior review worktree must not render files as binary under a
        PR-supplied `* -diff`."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        base_sha = _origin_main_sha(repo)
        first_result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["pr_file.txt"],
        )
        assert first_result.returncode == 0, first_result.stderr
        prior_review_worktree = _printed_worktree_path(first_result)
        (prior_review_worktree / ".gitattributes").write_text("* -diff\n")

        result, _ = _run(
            prior_review_worktree, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["pr_file.txt"],
        )

        assert result.returncode == 0, result.stderr
        diff_bytes = _printed_diff_path(result).read_bytes()
        assert b"+pr change" in diff_bytes
        assert diff_bytes == _independent_git_diff(repo, base_sha, pr_sha, isolated_home).stdout

        # Control: a plain diff run inside that worktree does read the attribute.
        diff_run_in_worktree = _independent_git_diff(prior_review_worktree, base_sha, pr_sha, isolated_home).stdout
        assert b"Binary files" in diff_run_in_worktree, "control: the worktree's .gitattributes is live"


def _worktree_porcelain_records(repo: Path) -> dict[Path, list[str]]:
    """Each worktree path `git worktree list --porcelain` reports for repo,
    resolved, mapped to the remaining lines of its record."""
    listing = subprocess.run(
        ["git", "worktree", "list", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    ).stdout
    records: dict[Path, list[str]] = {}
    for record in listing.strip().split("\n\n"):
        first_line, *attribute_lines = record.splitlines()
        records[Path(first_line.removeprefix("worktree ")).resolve()] = attribute_lines
    return records


def _first_live_linked_worktree(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'. {shlex.quote(str(HOOKS_DIR / "_lib.sh"))}; _lib_first_live_linked_worktree "$1"',
         "bash", str(repo.resolve())],
        capture_output=True, text=True, check=False, timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    )


class TestCheckoutWorktreeIsNotCountedAsLive:
    """marker.sh's main-tree refusal and the worktree-anchor nudge skip a
    review worktree through _lib_first_live_linked_worktree, which recognizes
    one by shape. This pins the shape the real script creates."""

    def test_the_created_worktree_is_detached_directly_under_the_worktrees_dir_and_is_skipped(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 0, result.stderr

        worktree = _printed_worktree_path(result).resolve()
        assert "detached" in _worktree_porcelain_records(repo)[worktree]
        assert worktree.parent == repo.resolve() / ".claude" / "worktrees"
        skipped = _first_live_linked_worktree(repo)
        assert skipped.returncode != 0, f"a review checkout must not count as live: {skipped.stdout!r}"
        assert skipped.stdout == ""

        # Control: a branch worktree beside it is still returned.
        branch_worktree = repo / ".claude" / "worktrees" / "feature"
        subprocess.run(
            ["git", "worktree", "add", "-b", "feature", str(branch_worktree)], cwd=repo, check=True,
            capture_output=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
        counted = _first_live_linked_worktree(repo)
        assert counted.returncode == 0
        assert counted.stdout == str(branch_worktree.resolve())


class TestAuditCleanProceedsToCheckout:
    def test_clean_audit_fetches_and_checks_out_the_pr_commit(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["src/app.py", "README.md"],
        )
        assert result.returncode == 0, result.stderr

        worktree_dir = _printed_worktree_path(result)
        assert [worktree_dir] == _review_worktrees(repo)
        assert (worktree_dir / "pr_file.txt").exists(), "worktree must hold the PR commit's own content"
        checked_out_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=worktree_dir, capture_output=True, text=True, check=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        ).stdout.strip()
        assert checked_out_head == pr_sha

        calls = _read_calls(call_log)
        assert any(c[:2] == ["pr", "view"] for c in calls), "headRefOid must be self-fetched"
        # Scoped to the files-listing call specifically, not api_calls[0] --
        # the trust-classification call is also a `gh api` call and runs
        # first.
        files_api_calls = [c for c in calls if _is_listing_call(c, "/files")]
        assert files_api_calls, "the file list must be self-fetched"
        assert "--paginate" in files_api_calls[0], (
            "the files listing call must paginate -- a regression dropping "
            "--paginate would silently cap the audited file list at one page"
        )

    def test_empty_file_list_is_clean_and_proceeds_to_checkout(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """A PR touching zero files (the boundary `gh api --paginate` itself
        can return) must audit clean, not be mistaken for the pagination
        failure this script also has to distinguish from "no files"."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=[]
        )
        assert result.returncode == 0, result.stderr
        assert [_printed_worktree_path(result)] == _review_worktrees(repo)

    def test_second_run_against_same_pr_gets_its_own_worktree(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """Every invocation creates a fresh mktemp worktree, so a second run
        never replaces or collides with the first run's."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        first, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["src/app.py"],
        )
        assert first.returncode == 0, first.stderr

        second, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["src/app.py"],
        )
        assert second.returncode == 0, second.stderr
        first_dir, second_dir = _printed_worktree_path(first), _printed_worktree_path(second)
        assert first_dir != second_dir
        assert _review_worktrees(repo) == sorted([first_dir, second_dir])
        for worktree_dir in (first_dir, second_dir):
            assert (worktree_dir / "pr_file.txt").exists()

    def test_force_pushed_pr_head_replaces_the_stale_local_ref_and_checks_out_the_new_head(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """A re-review after the PR's author force-pushed a head that is not a
        descendant of the earlier one: the local ref from the first run
        already exists at the old commit, and the fetch must move it, not
        reject the update as non-fast-forward."""
        _install_audit_script(isolated_home)
        repo, first_pr_sha = repo_with_pr_ref
        first, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=first_pr_sha, files=["src/app.py"],
        )
        assert first.returncode == 0, first.stderr

        # A sibling of the first PR commit (same parent), so the new head is not
        # a fast-forward of the old one.
        (repo / "pr_file_rewritten.txt").write_text("rewritten pr change\n")
        subprocess.run(["git", "add", "pr_file_rewritten.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "rewritten pr commit"], cwd=repo, check=True)
        rewritten_pr_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        ).stdout.strip()
        subprocess.run(
            ["git", "push", "-q", "origin", f"+HEAD:refs/pull/{PR_NUMBER}/head"], cwd=repo, check=True,
        )
        assert rewritten_pr_sha != first_pr_sha

        second, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=rewritten_pr_sha, files=["src/app.py"],
        )
        assert second.returncode == 0, second.stderr
        second_worktree = _printed_worktree_path(second)
        assert (second_worktree / "pr_file_rewritten.txt").exists()
        checked_out_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=second_worktree, capture_output=True, text=True, check=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        ).stdout.strip()
        assert checked_out_head == rewritten_pr_sha
        second_diff = _printed_diff_path(second).read_text()
        assert "+++ b/pr_file_rewritten.txt" in second_diff
        assert "+pr change" not in second_diff, "the diff file must replace the first run's, not keep it"


class TestCheckoutRunsNoGitHooks:
    # The base fetch's `-c core.hooksPath=/dev/null` has no test here. A fetch
    # by object name updates no ref, so it fires no reference-transaction hook
    # and no sentinel observes the flag.
    def test_no_git_hook_runs_during_the_pr_ref_fetch_or_the_worktree_add(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        hook_ran_sentinel = tmp_path / "hook-ran"
        post_checkout_hook = repo / ".git" / "hooks" / "post-checkout"
        post_checkout_hook.write_text(f"#!/bin/sh\ntouch '{hook_ran_sentinel}'\n")
        post_checkout_hook.chmod(0o755)
        # The PR-ref fetch updates a local ref, which fires reference-transaction.
        fetch_hook_ran_sentinel = tmp_path / "fetch-hook-ran"
        reference_transaction_hook = repo / ".git" / "hooks" / "reference-transaction"
        reference_transaction_hook.write_text(f"#!/bin/sh\ntouch '{fetch_hook_ran_sentinel}'\n")
        reference_transaction_hook.chmod(0o755)

        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["src/app.py"],
        )

        assert result.returncode == 0, result.stderr
        assert (_printed_worktree_path(result) / "pr_file.txt").exists()
        assert not hook_ran_sentinel.exists(), "the checkout must run with git hooks disabled"
        assert not fetch_hook_ran_sentinel.exists(), "the PR-ref fetch must run with git hooks disabled"

        # Control: the same hook does fire for a plain `git worktree add`.
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(tmp_path / "control-worktree"), pr_sha],
            cwd=repo, capture_output=True, check=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
        assert hook_ran_sentinel.exists(), "control: the hook is wired correctly, so its absence above is the -c flag's doing"

        # Control: the same reference-transaction hook does fire for a plain fetch of the PR ref.
        subprocess.run(
            ["git", "fetch", "origin", f"refs/pull/{PR_NUMBER}/head:refs/control/pr-{PR_NUMBER}"],
            cwd=repo, capture_output=True, check=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
        assert fetch_hook_ran_sentinel.exists(), "control: the hook is wired, so its absence above is the -c flag's doing"


class TestAuditStopAbortsBeforeFetch:
    def test_execution_surface_hit_aborts_with_no_fetch_of_the_pr_ref(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["src/app.py", ".mcp.json"],
        )
        assert result.returncode == 3, "an audit stop names review-pr-diff.sh, so it exits with the refusal status"
        assert ".mcp.json" in result.stderr
        assert "review-pr-diff.sh" in result.stderr

        # The property the whole design exists to guarantee: a stop verdict
        # must leave no trace of a fetch, not just report a matching exit code.
        assert _local_pr_ref_names(repo) == "", (
            "an audit-stop verdict must never fetch refs/pull/<N>/head into a local ref"
        )
        assert _review_worktrees(repo) == []

        # Both self-fetched facts (headRefOid, file list) had to be read
        # before the audit could run at all -- only the ref fetch is
        # forbidden past a stop verdict.
        calls = _read_calls(call_log)
        assert any(c[:2] == ["pr", "view"] for c in calls)
        assert any(c[:1] == ["api"] for c in calls)


class TestAuditRunsIsolatedFromPythonEnvironment:
    def test_a_json_module_planted_on_pythonpath_cannot_forge_a_clean_verdict(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """The audit gate runs `python3 -I`, so PYTHONPATH never reaches it. A
        plain `python3` would import the planted json, read the stop-worthy
        `.mcp.json` as clean, and let the PR's tree onto disk."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        sentinel = tmp_path / "poison-ran"
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["src/app.py", ".mcp.json"],
            extra_env=_pythonpath_env_that_forges_a_clean_audit(tmp_path / "poison", sentinel),
        )
        assert result.returncode == 3, result.stderr
        assert not sentinel.exists(), "the planted json module ran inside the audit"
        assert _review_worktrees(repo) == []


class TestAuditStopMessageKeepsEachMatchOnOneLine:
    def test_a_matched_path_holding_a_newline_and_an_escape_byte_is_printed_json_escaped(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """A PR file name is attacker-chosen, so a newline in a matched path
        must not start a second line that reads as the script's own output."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        injecting_name = "x\nreview-pr-checkout.sh: injected line/CLAUDE.md"
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["src/app.py", injecting_name],
        )
        assert result.returncode == 3
        assert json.dumps(injecting_name) in result.stderr
        assert "\nreview-pr-checkout.sh: injected line" not in result.stderr

    def test_matches_past_the_listing_limit_are_counted_not_printed(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        match_limit = _review_pr_audit_limit("REVIEW_PR_AUDIT_MATCH_LINE_LIMIT")
        unlisted_count = 5
        matching_names = [f"pkg_{index}/CLAUDE.md" for index in range(match_limit + unlisted_count)]
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=matching_names,
        )
        assert result.returncode == 3
        assert f'"pkg_{match_limit - 1}/CLAUDE.md"' in result.stderr
        assert f'"pkg_{match_limit}/CLAUDE.md"' not in result.stderr
        assert f"{unlisted_count} more matched path(s) not shown" in result.stderr


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
        assert result.returncode == 3, result.stderr
        assert (
            "the audit reported 2 matched path(s), but its match list could not be formatted for display"
            in result.stderr
        )
        assert "[1, 2]" not in result.stderr


class TestPrRefFetchFailure:
    @pytest.mark.parametrize(
        ("exit_status", "expect_cap_note"), [(1, False), (124, True)], ids=["plain_failure", "cap_kill_status"]
    )
    def test_fetch_failure_names_a_cap_kill_only_for_a_cap_kill_status(
        self, isolated_home, repo_with_pr_ref, tmp_path, exit_status, expect_cap_note
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        shim_dir = tmp_path / "git_fetch_shim"
        shim_dir.mkdir()
        git_shim = shim_dir / "git"
        git_shim.write_text(
            f'#!/usr/bin/env bash\nif [[ "$*" == *" fetch "* ]]; then exit {exit_status}; fi\n'
            f'exec {shlex.quote(shutil.which("git"))} "$@"\n'
        )
        git_shim.chmod(0o755)
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], path_prefix=shim_dir,
        )
        assert result.returncode == 2, result.stderr
        assert f"could not fetch refs/pull/{PR_NUMBER}/head" in result.stderr
        assert ("consistent with the" in result.stderr) is expect_cap_note
        assert _review_worktrees(repo) == []


class TestHeadRefOidMismatch:
    def test_force_push_race_aborts_with_no_worktree_left_behind(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """gh reports a headRefOid the actually-fetched refs/pull/<N>/head
        does not match -- a force-push landed between this script's own
        headRefOid fetch and its own ref fetch moments later."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid="f" * 40, files=["src/app.py"],
        )
        assert result.returncode == 2, result.stderr
        assert "between audit and checkout" in result.stderr
        assert _review_worktrees(repo) == []


class TestHeadRefOidDriftDuringFileListFetch:
    def test_headrefoid_drift_between_initial_fetch_and_file_list_fetch_aborts_before_audit(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """Distinct from TestHeadRefOidMismatch above (the gh-reported
        headRefOid vs. the actually-fetched ref, caught only at final
        checkout): this covers a force-push landing between the initial
        headRefOid fetch and the file-list fetch, before the audit even
        runs. Without the re-fetch-and-compare guard, the audit would run
        against a file list gh already considers stale, and the final
        checkout's own headRefOid comparison could pass anyway if the
        checkout's own ref fetch lands on that same later SHA -- the drifted
        file list would never actually have been audited."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, head_ref_oid_second="f" * 40, files=["src/app.py"],
        )
        assert result.returncode == 2, result.stderr
        assert "force-push" in result.stderr
        assert _local_pr_ref_names(repo) == "", (
            "a drift-detected-mid-audit abort must never fetch refs/pull/<N>/head into a local ref"
        )
        assert _review_worktrees(repo) == []

        calls = _read_calls(call_log)
        pr_view_calls = [c for c in calls if c[:2] == ["pr", "view"]]
        assert len(pr_view_calls) == 2, (
            "headRefOid must be fetched twice: once before the file list, "
            "once to re-verify it before the audit runs"
        )


class TestMalformedGhApiOutput:
    @pytest.mark.parametrize(
        "exit_status,status_wording",
        [(1, "failed (exit 1)"), (124, "timed out"), (137, "failed (exit 137)")],
        ids=["gh-failed", "cap-kill-124", "sigkill-137"],
    )
    def test_files_listing_failure_aborts_rather_than_auditing_an_empty_list(
        self, isolated_home, repo_with_pr_ref, tmp_path, exit_status, status_wording
    ):
        """`gh api --paginate` failing (a bad page mid-pagination, a rate
        limit, the cap firing) must abort outright, never fall through to
        auditing an empty file list as if the PR touched nothing."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, fail_files=True, files_failure_exit_status=exit_status,
        )
        assert result.returncode == 2
        assert "file list" in result.stderr
        assert f"gh api --paginate {status_wording}" in result.stderr
        assert _local_pr_ref_names(repo) == ""
        assert _review_worktrees(repo) == []

    def test_head_ref_oid_fetch_failure_aborts_before_any_files_audit(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, _ = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, fail_pr_view=True, files=["a.py"]
        )
        assert result.returncode == 2, result.stderr
        assert "headRefOid" in result.stderr
        assert _review_worktrees(repo) == []

    def test_partial_files_listing_before_failure_is_discarded(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """Distinct from the zero-output failure case above: `gh api
        --paginate` can print one or more pages successfully before a later
        page fails mid-stream, so the process's own nonzero exit is the only
        signal a partial (not empty) listing was produced. That partial
        listing must be discarded exactly like the zero-line case, never
        audited as if it were the PR's complete file set."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, partial_files_then_fail=["src/app.py", "README.md"],
        )
        assert result.returncode == 2, result.stderr
        assert "file list" in result.stderr
        assert _local_pr_ref_names(repo) == ""
        assert _review_worktrees(repo) == []


class TestFileListIntegrity:
    """The file listing is fetched one JSON string per name, so a name that
    holds a raw newline cannot split into two innocuous-looking fragments,
    and its length is checked against the PR's own REST `changed_files`. The
    input matrix for the count check lives in test_review_pr_lib.py."""

    def test_newline_embedded_filename_reaches_the_audit_as_one_name(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """The literal shape a raw `--jq '.[].filename'` fetch mishandles:
        one name whose newline a line-split turns into the two fragments
        `docs/notes.txt` and `evil.sh`. A stand-in audit records its stdin,
        since a clean exit alone cannot show the name arrived whole."""
        audit_stdin_record = tmp_path / "audit-stdin.json"
        _install_audit_script_that_runs(
            isolated_home,
            "import sys\n"
            f"open({str(audit_stdin_record)!r}, 'w').write(sys.stdin.read())\n"
            "print('{\"stop\": false, \"matches\": []}')\n",
        )
        repo, pr_sha = repo_with_pr_ref
        newline_name = "docs/notes.txt\nevil.sh"
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py", newline_name],
        )
        assert result.returncode == 0, result.stderr
        assert len(_review_worktrees(repo)) == 1
        assert json.loads(audit_stdin_record.read_text()) == ["a.py", newline_name]
        files_calls = [c for c in _read_calls(call_log) if _is_listing_call(c, "/files")]
        assert files_calls and ".[].filename | @json" in files_calls[0], (
            "the listing must be fetched one JSON string per name"
        )

    def test_printable_non_ascii_quote_and_space_filenames_check_out(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """Names that only look unusual are passed through unchanged."""
        audit_stdin_record = tmp_path / "audit-stdin.json"
        _install_audit_script_that_runs(
            isolated_home,
            "import sys\n"
            f"open({str(audit_stdin_record)!r}, 'w').write(sys.stdin.read())\n"
            "print('{\"stop\": false, \"matches\": []}')\n",
        )
        repo, pr_sha = repo_with_pr_ref
        unusual_names = ["docs/caf\u00e9 notes.md", 'say "hi".txt', "a.py"]
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=unusual_names,
        )
        assert result.returncode == 0, result.stderr
        assert [_printed_worktree_path(result)] == _review_worktrees(repo)
        assert json.loads(audit_stdin_record.read_text()) == unusual_names

    def test_listing_length_differing_from_changed_files_refuses_before_any_ref_fetch(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], changed_files=2,
        )
        assert result.returncode == 2
        assert "truncated, or the PR changed while it was being fetched" in result.stderr
        assert "Retry" in result.stderr
        assert _local_pr_ref_names(repo) == ""
        assert _review_worktrees(repo) == []

    def test_missing_changed_files_count_aborts_before_the_file_list_is_fetched(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], omit_changed_files=True,
        )
        assert result.returncode == 2
        assert "changed_files" in result.stderr
        assert not any(_is_listing_call(c, "/files") for c in _read_calls(call_log))
        assert _review_worktrees(repo) == []


class TestAuditRejectsMalformedInput:
    """AUDIT_EXIT == 2 (audit-execution-surface.py's own "stdin must be a
    JSON array of path strings" rejection) is unreachable through this
    script's ordinary pipeline: `@json` output decoded by `jq -c -s '.'`
    structurally always yields a JSON array of strings from any text
    `gh api` can print, so the classifier's own non-string-element rejection
    has no real trigger short of corrupting that decode step. A `jq` PATH
    shim does that corruption deliberately -- and delegates every other jq
    call to the real binary -- so the real classifier (not a stand-in) is
    what's driven to exit 2."""

    def test_corrupted_jq_encoding_makes_audit_reject_non_string_array(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        call_log = tmp_path / "gh_calls.jsonl"
        env = _shimmed_env(
            tmp_path,
            _gh_shim_source(
                call_log, head_ref_oid=pr_sha, files=["a.py"],
                # The corrupted decode below yields three elements; the count
                # check must pass so the run reaches the audit.
                changed_files=3, base_ref_oid=_origin_main_sha(repo),
            ),
        )
        env["HOME"] = str(isolated_home)
        env.pop("CLAUDE_CONFIG_DIR", None)

        real_jq = shutil.which("jq")
        assert real_jq is not None, "the real jq binary is required to delegate to"
        jq_shim_dir = tmp_path / "jq-shim"
        jq_shim_dir.mkdir()
        jq_shim = jq_shim_dir / "jq"
        jq_shim.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env python3
            # Test-only override of jq for review-pr-checkout.sh's FILES_JSON
            # decode step (`jq -c -s .`) -- emits a JSON array of non-string
            # elements instead, so audit-execution-surface.py's own "must be
            # an array of strings" rejection (otherwise unreachable through
            # this script's real pipeline) fires on real, self-fetched
            # input. Every other jq call runs the real binary.
            import os
            import sys
            if sys.argv[1:] == ["-c", "-s", "."]:
                print("[1, 2, 3]")
                sys.exit(0)
            os.execv({real_jq!r}, ["jq", *sys.argv[1:]])
        """))
        jq_shim.chmod(0o755)
        env["PATH"] = os.pathsep.join([str(jq_shim_dir), env["PATH"]])

        result = subprocess.run(
            ["bash", str(SCRIPT), PR_IDENTITY], cwd=repo, env=env, capture_output=True, text=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
        _assert_gh_calls_are_read_only(_read_calls(call_log), known_api_endpoint=_KNOWN_API_ENDPOINT)
        assert result.returncode == 2, result.stderr
        assert (
            'returned no verdict (exit 2, first line of stdout: {"error": "stdin must be a JSON array of path strings"})'
            in result.stderr
        )
        assert _review_worktrees(repo) == []


class TestAuditThatReturnsNoVerdictIsNotARefusal:
    """Exit 3 tells the caller to switch to review-pr-diff.sh, so it is
    reserved for an audit that positively returned `stop: true`. A tool that
    failed to run is an operational failure, exit 2 with the audit's stderr
    shown."""

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
    def test_a_failed_audit_exits_two_never_three_and_fetches_nothing(
        self, isolated_home, repo_with_pr_ref, tmp_path, audit_script_body
    ):
        _install_audit_script_that_runs(isolated_home, audit_script_body)
        repo, pr_sha = repo_with_pr_ref
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["src/app.py"],
        )
        assert result.returncode == 2, result.stderr
        assert "returned no verdict (exit " in result.stderr
        assert "review-pr-diff.sh" not in result.stderr, "a tooling failure must not tell the caller to switch paths"
        assert _local_pr_ref_names(repo) == ""
        assert _review_worktrees(repo) == []

    def test_the_failed_audits_stderr_is_shown(self, isolated_home, repo_with_pr_ref, tmp_path):
        _install_audit_script_that_runs(isolated_home, "raise RuntimeError('audit-tool-crashed')\n")
        repo, pr_sha = repo_with_pr_ref
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["src/app.py"],
        )
        assert result.returncode == 2
        assert "audit-tool-crashed" in result.stderr

    def test_a_positive_stop_verdict_exits_three(self, isolated_home, repo_with_pr_ref, tmp_path):
        _install_audit_script_that_runs(
            isolated_home,
            "import sys\n"
            "print('{\"stop\": true, \"matches\": [{\"path\": \"src/app.py\", \"reason\": \"stand-in reason\"}]}')\n"
            "sys.exit(1)\n",
        )
        repo, pr_sha = repo_with_pr_ref
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["src/app.py"],
        )
        assert result.returncode == 3
        assert '"src/app.py": stand-in reason' in result.stderr
        assert "review-pr-diff.sh" in result.stderr
        assert _local_pr_ref_names(repo) == ""


class TestAuditThatExitsCleanWithoutAVerdictIsNotClean:
    """Exit status 0 alone is not a clean audit: an empty or truncated audit
    script also exits 0. Only the audit's exact clean document proceeds to the
    ref fetch, so an unaudited PR is never checked out."""

    @pytest.mark.parametrize(
        "audit_script_body,first_stdout_line",
        [
            ("", ""),
            ("print('not json')\nprint('second line')\n", "not json"),
            ("print('{\"stop\": true, \"matches\": []}')\n", '{"stop": true, "matches": []}'),
        ],
        ids=["empty-script", "non-json-stdout", "stop-true-at-status-0"],
    )
    def test_a_zero_status_audit_without_a_clean_verdict_exits_two_and_checks_nothing_out(
        self, isolated_home, repo_with_pr_ref, tmp_path, audit_script_body, first_stdout_line
    ):
        _install_audit_script_that_runs(isolated_home, audit_script_body)
        repo, pr_sha = repo_with_pr_ref
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["src/app.py"],
        )
        assert result.returncode == 2, result.stderr
        assert f"returned no verdict (exit 0, first line of stdout: {first_stdout_line})" in result.stderr
        assert "second line" not in result.stderr
        assert "review-pr-diff.sh" not in result.stderr, "a tooling failure must not tell the caller to switch paths"
        assert result.stdout == ""
        assert _local_pr_ref_names(repo) == ""
        assert _review_worktrees(repo) == []


class TestStandInAuditInstaller:
    def test_installing_a_stand_in_replaces_the_audit_symlink_without_writing_through_it(
        self, isolated_home, tmp_path
    ):
        """The symlink points at a scratch copy, so a regression that writes
        through it damages only the copy, never the tracked audit script."""
        scratch_audit_script = tmp_path / "scratch-audit-execution-surface.py"
        real_audit_source = (SKILLS_DIR / "review-pr" / "audit-execution-surface.py").read_text()
        scratch_audit_script.write_text(real_audit_source)
        _install_audit_script(isolated_home, scratch_audit_script)
        installed = isolated_home / ".claude" / "skills" / "review-pr" / "audit-execution-surface.py"
        assert installed.is_symlink()

        _install_audit_script_that_runs(isolated_home, "stand-in body\n")

        assert installed.read_text() == "stand-in body\n"
        assert not installed.is_symlink()
        assert scratch_audit_script.read_text() == real_audit_source

    def test_reinstalling_the_real_audit_replaces_an_earlier_stand_in(self, isolated_home):
        _install_audit_script_that_runs(isolated_home, "stand-in body\n")

        _install_audit_script(isolated_home)

        installed = isolated_home / ".claude" / "skills" / "review-pr" / "audit-execution-surface.py"
        assert installed.is_symlink()
        assert installed.read_text() == (SKILLS_DIR / "review-pr" / "audit-execution-surface.py").read_text()


class TestRepoReadThatCannotRunIsNotACrossRepositoryRefusal:
    @pytest.mark.parametrize("failing_read", [".head.repo", ".base.repo"], ids=["head-read", "base-read"])
    def test_a_jq_failure_reading_either_repo_exits_two_never_three(
        self, isolated_home, repo_with_pr_ref, tmp_path, failing_read
    ):
        """A null head.repo (a deleted fork) is a positive cross-repository
        verdict, exit 3. A jq that fails on either repo read produced no
        verdict, so the caller must not be told to switch to
        review-pr-diff.sh: with a failed base read and a real head name the
        two would read as unequal. Every other jq call runs the real binary."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        real_jq = shutil.which("jq")
        assert real_jq is not None, "the real jq binary is required to delegate to"
        jq_shim_dir = tmp_path / "jq-shim"
        jq_shim_dir.mkdir()
        jq_shim = jq_shim_dir / "jq"
        jq_shim.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import os
            import sys
            if any({failing_read!r} in arg for arg in sys.argv[1:]):
                sys.exit(5)
            os.execv({real_jq!r}, ["jq", *sys.argv[1:]])
        """))
        jq_shim.chmod(0o755)

        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], path_prefix=jq_shim_dir,
        )
        assert result.returncode == 2, result.stderr
        assert "could not read" in result.stderr
        assert "undecided" in result.stderr
        assert "review-pr-diff.sh" not in result.stderr
        assert not any(_is_listing_call(c, "/files") for c in _read_calls(call_log))
        assert _local_pr_ref_names(repo) == ""
        assert _review_worktrees(repo) == []


def _shim_that_fails(tmp_path: Path, tool: str) -> Path:
    """A PATH shim directory holding one `tool` that always exits 1."""
    shim_dir = tmp_path / f"{tool}_failing_shim"
    shim_dir.mkdir()
    shim = shim_dir / tool
    shim.write_text(f"#!/usr/bin/env bash\necho 'synthetic {tool} failure' >&2\nexit 1\n")
    shim.chmod(0o755)
    return shim_dir


class TestWorktreeAddFailure:
    def test_failed_git_worktree_add_aborts_and_leaves_no_directory_behind(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """The mktemp directory exists before `git worktree add` runs, so a
        failed add must remove it: review-pr-finish.sh discovers worktrees
        only through `git worktree list`, so it never sweeps a leftover
        directory git did not register."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"],
            path_prefix=_git_shim_that_fails_on_worktree_subcommand(tmp_path, "add"),
        )
        assert result.returncode == 2, result.stderr
        assert "git worktree add failed" in result.stderr
        assert "local git cap" not in result.stderr
        assert result.stdout == ""
        assert _review_worktrees(repo) == []

    def test_failed_add_leaves_the_acquire_step_provenance_untouched(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """Provenance is rewritten to mode `checkout` only after the add
        succeeds: nothing local corroborates that mode afterwards, so a
        failed add must not have recorded it."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        provenance = write_review_pr_provenance(
            isolated_home, PR_IDENTITY, pr_sha, 999, mode="acquired", session_id=SID
        )
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"],
            path_prefix=_git_shim_that_fails_on_worktree_subcommand(tmp_path, "add"),
        )
        assert result.returncode == 2, result.stderr
        assert _provenance_fields(provenance)["mode"] == "acquired"

    def test_add_exit_status_from_a_cap_kill_is_named_in_the_message(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """One status exercises the message branch; _lib_status_consistent_with_cap_kill
        has its own direct tests for the full status set."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"],
            path_prefix=_git_shim_that_fails_on_worktree_subcommand(tmp_path, "add", exit_status=124),
        )
        assert result.returncode == 2
        assert "exited 124" in result.stderr
        assert "local git cap" in result.stderr
        assert "review-pr-finish.sh" in result.stderr
        assert result.stdout == ""
        assert _review_worktrees(repo) == []


    def test_failed_add_deletes_only_the_fresh_directory_not_an_earlier_same_session_worktree(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """The failure-path `rm -rf` targets the fresh mktemp directory alone.
        An earlier run's worktree for the same session and PR carries the same
        name shape, so a widened delete would take it. The diff file's content
        after the failed second run is unspecified, and review-pr-finish.sh
        removes it."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        first, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"],
        )
        assert first.returncode == 0, first.stderr
        earlier_worktree = _printed_worktree_path(first)

        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"],
            path_prefix=_git_shim_that_fails_on_worktree_subcommand(tmp_path, "add"),
        )

        assert result.returncode == 2
        assert result.stdout == ""
        assert "git worktree add failed" in result.stderr
        assert _review_worktrees(repo) == [earlier_worktree], "only the fresh directory is deleted"
        assert (earlier_worktree / "pr_file.txt").exists()
        assert earlier_worktree.resolve() in _registered_worktree_paths(repo)


class TestSessionArtifactWriteFailure:
    def test_a_file_where_the_active_directory_belongs_aborts_before_any_worktree(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """`mkdir -p` cannot create a directory over a regular file. Under
        `set -e` an unguarded call would exit with mkdir's own status 1. The
        diff is written before the worktree is created, so none exists."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        (isolated_home / ".claude").mkdir(exist_ok=True)
        (isolated_home / ".claude" / ".review-pr-active.d").write_text("not a directory\n")
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 2
        assert "could not create the active directory" in result.stderr
        assert result.stdout == ""
        assert _review_worktrees(repo) == []

    def test_a_symlink_where_the_diff_file_belongs_aborts_before_any_worktree(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """The diff write refuses a symlink at its predictable destination
        rather than truncating the symlink's target."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        symlink_target = tmp_path / "symlink_target"
        symlink_target.write_text("pre-existing content\n")
        active_dir = isolated_home / ".claude" / ".review-pr-active.d"
        active_dir.mkdir(parents=True)
        (active_dir / f"{SID}.diff").symlink_to(symlink_target)
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 2
        assert "could not write diff file" in result.stderr
        assert result.stdout == ""
        assert symlink_target.read_text() == "pre-existing content\n", "the write must not follow the symlink"
        assert _review_worktrees(repo) == []

    def test_a_symlink_where_the_provenance_file_belongs_aborts_with_exit_two(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """The provenance write refuses a symlink at its destination. Under
        `set -e` an unguarded call would exit with the write pipeline's own
        status 1. The worktree already exists by then, and the message names
        finish as the cleanup."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        symlink_target = tmp_path / "symlink_target"
        active_dir = isolated_home / ".claude" / ".review-pr-active.d"
        active_dir.mkdir(parents=True)
        (active_dir / f"{SID}.provenance").symlink_to(symlink_target)
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 2
        assert "could not write provenance file" in result.stderr
        assert "review-pr-finish.sh" in result.stderr
        assert result.stdout == ""
        assert not symlink_target.exists(), "the write must not follow the symlink"
        assert len(_review_worktrees(repo)) == 1


class TestWorktreeDirectoryCreationFailure:
    """The worktree's parent directory or its mktemp directory cannot be
    created: the script aborts through its own exit-2 contract, before git
    is asked to add anything and before provenance is rewritten."""

    def test_mktemp_failure_aborts_with_exit_two_and_no_provenance(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"],
            path_prefix=_shim_that_fails(tmp_path, "mktemp"),
        )
        assert result.returncode == 2
        assert "could not create a directory for the review worktree" in result.stderr
        assert result.stdout == ""
        assert not (isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.provenance").exists()
        assert _review_worktrees(repo) == []

    def test_a_file_where_the_worktrees_directory_belongs_aborts_with_exit_two(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """`mkdir -p` cannot create a directory over a regular file. Under
        `set -e` an unguarded call would exit with mkdir's own status 1."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        (repo / ".claude").mkdir(exist_ok=True)
        (repo / ".claude" / "worktrees").write_text("not a directory\n")
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 2
        assert "could not create a directory for the review worktree" in result.stderr
        assert result.stdout == ""
        assert not (isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.provenance").exists()
