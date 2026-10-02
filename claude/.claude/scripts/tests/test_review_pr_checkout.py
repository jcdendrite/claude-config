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
import shlex
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
from helpers import (
    SCRIPTS_DIR,
    SKILLS_DIR,
    write_review_pr_provenance,
)

from .conftest import (
    GH_CONTROL_CHARACTER_SANITIZER_SHIM_LINE,
    _build_repo_with_pr_ref,
    _git_shim_that_fails_on_worktree_subcommand,
    _install_audit_script,
    _install_audit_script_that_runs,
    _provenance_fields,
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
) -> str:
    """gh shim recording every invocation, dispatching on the invocation's own
    first word(s): `gh pr view ... --json headRefOid` returns `head_ref_oid`
    (`head_ref_oid_second` from the SECOND call onward, modeling a force-push
    between the initial fetch and the pre-audit re-fetch); `gh api
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
            if oid:
                print(oid)
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
            payload = {{
                "author_association": AUTHOR_ASSOCIATION,
                "head": {{"repo": head_repo}},
                "base": {{"repo": {{"full_name": base_name}}}},
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
    extra_env: dict | None = None,
    path_prefix: Path | None = None,
) -> tuple[subprocess.CompletedProcess, Path]:
    # A successful checkout writes provenance, which needs a live session
    # -- seeded unconditionally, harmlessly idempotent for the many tests
    # here that abort before ever reaching that write.
    _seed_session(home, SID)
    call_log = tmp_path / "gh_calls.jsonl"
    env = {
        **_shimmed_env(
            tmp_path,
            _gh_shim_source(
                call_log, head_ref_oid, files, fail_pr_view, fail_files, partial_files_then_fail,
                head_ref_oid_second, author_association, head_repo_full_name, base_repo_full_name,
                fail_trust_check, malformed_trust_check, changed_files, omit_changed_files,
                files_failure_exit_status,
            ),
        ),
        "HOME": str(home),
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
        worktree_dir = Path(result.stdout.strip())
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
        assert [Path(result.stdout.strip())] == _review_worktrees(repo)

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
        discipline this plan states elsewhere, extended to this call."""
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
        added anywhere in the file, while the completion marker still uses
        the older 4-line positional schema (mode is its LAST field, not
        merely index 3) -- a future field inserted there must not silently
        break marker.sh's own sed -n '4p' / this test's lines[-1] read."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 0, result.stderr
        worktree_dir = Path(result.stdout.strip())

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

        actual_worktree_dir = Path(result.stdout.strip())
        assert [actual_worktree_dir] == _review_worktrees(repo)
        assert actual_worktree_dir.exists()
        assert not str(actual_worktree_dir).startswith(str(linked_worktree))


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

        worktree_dir = Path(result.stdout.strip())
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
        assert [Path(result.stdout.strip())] == _review_worktrees(repo)

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
        first_dir, second_dir = Path(first.stdout.strip()), Path(second.stdout.strip())
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
        second_worktree = Path(second.stdout.strip())
        assert (second_worktree / "pr_file_rewritten.txt").exists()
        checked_out_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=second_worktree, capture_output=True, text=True, check=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        ).stdout.strip()
        assert checked_out_head == rewritten_pr_sha


class TestCheckoutRunsNoGitHooks:
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
        assert (Path(result.stdout.strip()) / "pr_file.txt").exists()
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
        assert [Path(result.stdout.strip())] == _review_worktrees(repo)
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
        env = _shimmed_env(
            tmp_path,
            _gh_shim_source(
                tmp_path / "gh_calls.jsonl", head_ref_oid=pr_sha, files=["a.py"],
                # The corrupted decode below yields three elements; the count
                # check must pass so the run reaches the audit.
                changed_files=3,
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
        assert "src/app.py: stand-in reason" in result.stderr
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
        assert _review_worktrees(repo) == []


    def test_failed_add_deletes_only_the_fresh_directory_not_an_earlier_same_session_worktree(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """The failure-path `rm -rf` targets the fresh mktemp directory alone.
        An earlier run's worktree for the same session and PR carries the same
        name shape, so a widened delete would take it."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        first, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"],
        )
        assert first.returncode == 0, first.stderr
        earlier_worktree = Path(first.stdout.strip())

        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"],
            path_prefix=_git_shim_that_fails_on_worktree_subcommand(tmp_path, "add"),
        )

        assert result.returncode == 2
        assert "git worktree add failed" in result.stderr
        assert _review_worktrees(repo) == [earlier_worktree], "only the fresh directory is deleted"
        assert (earlier_worktree / "pr_file.txt").exists()
        assert earlier_worktree.resolve() in _registered_worktree_paths(repo)


class TestProvenanceDirectoryCreationFailure:
    def test_a_file_where_the_provenance_directory_belongs_aborts_with_exit_two(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """`mkdir -p` cannot create a directory over a regular file. Under
        `set -e` an unguarded call would exit with mkdir's own status 1. The
        worktree already exists by then, and the message names finish as the
        cleanup."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        (isolated_home / ".claude").mkdir(exist_ok=True)
        (isolated_home / ".claude" / ".review-pr-active.d").write_text("not a directory\n")
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 2
        assert "could not create the provenance directory" in result.stderr
        assert "review-pr-finish.sh" in result.stderr
        assert result.stdout == ""
        assert len(_review_worktrees(repo)) == 1

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
