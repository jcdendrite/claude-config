"""Tests for review-pr-checkout.sh -- the single script that makes
/review-pr Step 2's stop-before-checkout invariant unbypassable by
construction: it re-derives the PR's file list and headRefOid itself from
`gh`/git rather than trusting either as an argument.

The `gh` CLI is replaced by a PATH shim that records every invocation it
receives (one JSON object per line), matching test_review_pr_post.py's own
shim pattern -- git is left real (not shimmed), so the fetch/checkout
assertions below check actual repository state (a local ref landing, a
worktree materializing) rather than a mocked call.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from helpers import SCRIPTS_DIR

from .conftest import _build_repo_with_pr_ref, _install_audit_script, _seed_session, _shimmed_env

SCRIPT = SCRIPTS_DIR / "review-pr-checkout.sh"
OWNER_REPO = "foo/bar"
PR_NUMBER = "42"
PR_IDENTITY = f"{OWNER_REPO}#{PR_NUMBER}"
SID = "test-session-review-pr-checkout"
_ATTRIBUTION_TRAILER = "🤖 Generated with [Claude Code](https://claude.com/claude-code)"


def _worktree_dir(repo: Path) -> Path:
    return repo / ".claude" / "worktrees" / f"review-pr-{OWNER_REPO.replace('/', '%')}-{PR_NUMBER}"


def _local_pr_ref_names(repo: Path) -> str:
    return subprocess.run(
        ["git", "for-each-ref", "refs/review-pr"], cwd=repo, capture_output=True, text=True, check=True
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


def _gh_shim_source(
    call_log: Path,
    head_ref_oid: str | None = None,
    files: list[str] | None = None,
    fail_pr_view: bool = False,
    fail_files: bool = False,
    partial_files_then_fail: list[str] | None = None,
    head_ref_oid_second: str | None = None,
    author_association: str = "MEMBER",
    head_repo_full_name: str | None = _SAME_AS_REQUEST,
    base_repo_full_name: str | None = _SAME_AS_REQUEST,
    fail_trust_check: bool = False,
    malformed_trust_check: bool = False,
) -> str:
    """gh shim recording every invocation, matching test_review_pr_post.py's
    own shim shape. Dispatches on the invocation's own first word(s):
    `gh pr view ... --json headRefOid` returns `head_ref_oid`; `gh api
    .../pulls/N` (no trailing `/files`) returns the trust-classification
    payload built from `author_association`/`head_repo_full_name`/
    `base_repo_full_name` -- both default to `_SAME_AS_REQUEST`, echoing
    back the request path's own owner/repo, so every pre-existing test
    below that doesn't care about trust classification still reaches
    checkout unchanged regardless of which owner/repo it uses; `gh api
    .../files --paginate ...` prints `files`, one per line. `fail_pr_view`/
    `fail_files` exit 1 on the matching call only, modeling a `gh` failure
    (rate limit, network) on that one endpoint without the production `gh`
    binary's own always-0 stand-in masking the script's failure branch.
    `partial_files_then_fail` prints those filenames, then exits 1 --
    `--paginate` failing partway through, after already emitting one or more
    pages, distinct from `fail_files`'s zero-output failure. `head_ref_oid_second`,
    when given, is returned by the SECOND `pr view` call onward instead of
    `head_ref_oid` -- models a force-push landing between
    review-pr-checkout.sh's initial headRefOid fetch and its own re-fetch of
    it just before the audit runs. `head_repo_full_name=None` models a
    deleted-fork PR (REST `head.repo` reads null). `fail_trust_check` exits
    1 on the trust-check call only; `malformed_trust_check` exits 0 but
    prints non-JSON, modeling a malformed response distinct from an
    outright `gh` failure."""
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
            "GH_HOST": os.environ.get("GH_HOST"),
            "GH_ENTERPRISE_TOKEN": os.environ.get("GH_ENTERPRISE_TOKEN"),
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
        if args[:1] == ["api"] and len(args) >= 2 and not args[1].endswith("/files"):
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
            print(json.dumps({{
                "author_association": AUTHOR_ASSOCIATION,
                "head": {{"repo": head_repo}},
                "base": {{"repo": {{"full_name": base_name}}}},
            }}))
            sys.exit(0)
        if args[:1] == ["api"]:
            if PARTIAL_FILES_THEN_FAIL:
                for f in PARTIAL_FILES_THEN_FAIL:
                    print(f)
                sys.exit(1)
            if FAIL_FILES:
                sys.exit(1)
            for f in FILES:
                print(f)
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
    author_association: str = "MEMBER",
    head_repo_full_name: str | None = _SAME_AS_REQUEST,
    base_repo_full_name: str | None = _SAME_AS_REQUEST,
    fail_trust_check: bool = False,
    malformed_trust_check: bool = False,
    extra_env: dict | None = None,
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
                fail_trust_check, malformed_trust_check,
            ),
        ),
        "HOME": str(home),
    }
    env.pop("CLAUDE_CONFIG_DIR", None)
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
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
        """Bounds the tightened owner/repo regex from the other side of
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
        assert result.returncode != 0
        assert "origin" in result.stderr
        assert _read_calls(call_log) == []
        assert _local_pr_ref_names(repo) == ""
        assert not _worktree_dir(repo).exists()


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
        assert result.returncode != 0
        assert "audit script not found" in result.stderr
        assert _read_calls(call_log) == []


class TestTrustClassificationRefuses:
    """Script exit-code tests with a PATH-shimmed `gh`, not hook-deny tests
    -- the trust block lives in the script by design (a hook cannot see a
    subprocess, and cannot verify the audit's own input). Trust
    classification widens the stop conditions; it never removes one."""

    @pytest.mark.parametrize("author_association", ["FIRST_TIME_CONTRIBUTOR", "NONE"])
    def test_restricted_author_association_refuses_with_no_fetch_or_worktree(
        self, isolated_home, repo_with_pr_ref, tmp_path, author_association
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], author_association=author_association,
        )
        assert result.returncode != 0
        assert "review-pr-diff.sh" in result.stderr
        assert _local_pr_ref_names(repo) == ""
        assert not _worktree_dir(repo).exists()
        # No files pagination either -- the block fires before it.
        assert not any(c[:1] == ["api"] and c[1].endswith("/files") for c in _read_calls(call_log))

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
        assert result.returncode != 0
        assert "review-pr-diff.sh" in result.stderr
        assert _local_pr_ref_names(repo) == ""
        assert not _worktree_dir(repo).exists()

    def test_deleted_fork_null_head_repo_refuses(self, isolated_home, repo_with_pr_ref, tmp_path):
        """A null `head.repo` (REST payload) means the PR's fork was
        deleted -- treated as cross-repo, the more restrictive read on an
        ambiguous input, never as a pass."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], head_repo_full_name=None,
        )
        assert result.returncode != 0
        assert "review-pr-diff.sh" in result.stderr
        assert not _worktree_dir(repo).exists()

    @pytest.mark.parametrize("author_association", ["MEMBER", "OWNER"])
    def test_member_or_owner_author_paired_with_cross_repo_still_refuses(
        self, isolated_home, repo_with_pr_ref, tmp_path, author_association
    ):
        """A MEMBER/OWNER author association must not itself waive the
        cross-repo check -- otherwise a suite that only checks cross-repo
        status for non-members would pass, which is the exact
        standing-gated shape this design rejects."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], author_association=author_association,
            head_repo_full_name="some-fork/bar", base_repo_full_name=OWNER_REPO,
        )
        assert result.returncode != 0
        assert "review-pr-diff.sh" in result.stderr
        assert not _worktree_dir(repo).exists()

    def test_member_same_repo_still_checks_out(self, isolated_home, repo_with_pr_ref, tmp_path):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], author_association="MEMBER",
        )
        assert result.returncode == 0, result.stderr
        assert Path(result.stdout.strip()) == _worktree_dir(repo)

    def test_trust_block_fires_before_the_paginated_file_list_call(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], author_association="NONE",
        )
        assert result.returncode != 0
        calls = _read_calls(call_log)
        assert calls, "the trust-check call itself must still have been made"
        assert calls[0][:1] == ["api"] and not calls[0][1].endswith("/files"), (
            "the trust-classification call must be the first gh invocation, "
            "strictly before the paginated files listing"
        )
        assert not any(c[1].endswith("/files") for c in calls if c[:1] == ["api"])

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
        assert result.returncode != 0
        assert not _worktree_dir(repo).exists()

    def test_trust_check_malformed_response_aborts(self, isolated_home, repo_with_pr_ref, tmp_path):
        """A malformed (non-JSON) trust-check response is distinct from an
        outright `gh` failure -- both must abort, never fall through."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], malformed_trust_check=True,
        )
        assert result.returncode != 0
        assert not _worktree_dir(repo).exists()


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
        lines = provenance.read_text().splitlines()
        assert lines[0] == PR_IDENTITY
        assert lines[1] == pr_sha
        assert lines[2].isdigit()
        assert lines[3] == "checkout"

    def test_successful_checkout_then_marker_write_pins_mode_as_last_line_of_both_files(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """Pins the shared 4-line schema convention across the two
        artifacts review-pr-checkout.sh and marker.sh's `write review-pr`
        arm produce: mode is the LAST positional field in both the
        provenance file and the completion marker, not merely field index
        3 -- a future field inserted at either end must not silently break
        either side's own sed -n '4p' (marker.sh) / lines[-1] read."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 0, result.stderr
        worktree_dir = Path(result.stdout.strip())

        provenance = isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.provenance"
        assert provenance.read_text().splitlines()[-1] == "checkout"

        findings_body = isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.body"
        findings_body.write_text(f"**[Claude Code]** # findings\n\n{_ATTRIBUTION_TRAILER}\n")

        marker_env = {"HOME": str(isolated_home)}
        marker_env.pop("CLAUDE_CONFIG_DIR", None)
        marker_result = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "marker.sh"), "write", "review-pr"],
            cwd=worktree_dir, env=marker_env, capture_output=True, text=True,
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
        assert result.returncode != 0
        provenance = isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.provenance"
        assert not provenance.exists()


class TestInvokedFromALinkedWorktree:
    def test_worktree_dir_lands_under_the_main_tree_not_the_linked_worktree(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """review-pr-checkout.sh must anchor WORKTREE_DIR under the main
        tree even when the session invoking it is standing in a linked
        worktree of the same repo, matching review-pr-finish.sh's own
        independent reconstruction of the identical path -- a divergence
        here orphans the review worktree on cleanup, since finish.sh's
        existence guard would check the wrong path."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        linked_worktree = tmp_path / "session-worktree"
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(linked_worktree), "HEAD"],
            cwd=repo, check=True,
        )

        result, call_log = _run(
            linked_worktree, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 0, result.stderr

        expected_worktree_dir = _worktree_dir(repo)
        actual_worktree_dir = Path(result.stdout.strip())
        assert actual_worktree_dir == expected_worktree_dir
        assert expected_worktree_dir.exists()
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
        assert worktree_dir == _worktree_dir(repo)
        assert (worktree_dir / "pr_file.txt").exists(), "worktree must hold the PR commit's own content"
        checked_out_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=worktree_dir, capture_output=True, text=True, check=True
        ).stdout.strip()
        assert checked_out_head == pr_sha

        calls = _read_calls(call_log)
        assert any(c[:2] == ["pr", "view"] for c in calls), "headRefOid must be self-fetched"
        # Scoped to the files-listing call specifically, not api_calls[0] --
        # the trust-classification call is also a `gh api` call and runs
        # first.
        files_api_calls = [c for c in calls if c[:1] == ["api"] and c[1].endswith("/files")]
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
        assert Path(result.stdout.strip()) == _worktree_dir(repo)

    def test_second_run_against_same_pr_replaces_prior_worktree(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
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
        assert Path(second.stdout.strip()) == _worktree_dir(repo)


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
        assert result.returncode != 0
        assert ".mcp.json" in result.stderr

        # The property the whole design exists to guarantee: a stop verdict
        # must leave no trace of a fetch, not just report a matching exit code.
        assert _local_pr_ref_names(repo) == "", (
            "an audit-stop verdict must never fetch refs/pull/<N>/head into a local ref"
        )
        assert not _worktree_dir(repo).exists()

        # Both self-fetched facts (headRefOid, file list) had to be read
        # before the audit could run at all -- only the ref fetch is
        # forbidden past a stop verdict.
        calls = _read_calls(call_log)
        assert any(c[:2] == ["pr", "view"] for c in calls)
        assert any(c[:1] == ["api"] for c in calls)


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
        assert result.returncode != 0
        assert "force-push" in result.stderr or "headRefOid" in result.stderr
        assert not _worktree_dir(repo).exists()


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
        assert result.returncode != 0
        assert "force-push" in result.stderr
        assert _local_pr_ref_names(repo) == "", (
            "a drift-detected-mid-audit abort must never fetch refs/pull/<N>/head into a local ref"
        )
        assert not _worktree_dir(repo).exists()

        calls = _read_calls(call_log)
        pr_view_calls = [c for c in calls if c[:2] == ["pr", "view"]]
        assert len(pr_view_calls) == 2, (
            "headRefOid must be fetched twice: once before the file list, "
            "once to re-verify it before the audit runs"
        )


class TestMalformedGhApiOutput:
    def test_files_listing_failure_aborts_rather_than_auditing_an_empty_list(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """`gh api --paginate` failing (a bad page mid-pagination, a rate
        limit) must abort outright, never fall through to auditing an empty
        file list as if the PR touched nothing."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, fail_files=True,
        )
        assert result.returncode != 0
        assert "file list" in result.stderr
        assert _local_pr_ref_names(repo) == ""
        assert not _worktree_dir(repo).exists()

    def test_head_ref_oid_fetch_failure_aborts_before_any_files_audit(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, _ = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, fail_pr_view=True, files=["a.py"]
        )
        assert result.returncode != 0
        assert "headRefOid" in result.stderr
        assert not _worktree_dir(repo).exists()

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
        assert result.returncode != 0
        assert "file list" in result.stderr
        assert _local_pr_ref_names(repo) == ""
        assert not _worktree_dir(repo).exists()


class TestAuditRejectsMalformedInput:
    """AUDIT_EXIT == 2 (audit-execution-surface.py's own "stdin must be a
    JSON array of path strings" rejection) is unreachable through this
    script's ordinary pipeline: `jq -R -s 'split("\\n") |
    map(select(length > 0))'` structurally always yields a JSON array of
    strings from any text `gh api` can print, so the classifier's own
    non-string-element rejection has no real trigger short of corrupting
    that encoding step. A `jq` PATH shim does that corruption deliberately,
    so the real classifier (not a stand-in) is what's driven to exit 2."""

    def test_corrupted_jq_encoding_makes_audit_reject_non_string_array(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        env = _shimmed_env(
            tmp_path,
            _gh_shim_source(tmp_path / "gh_calls.jsonl", head_ref_oid=pr_sha, files=["a.py"]),
        )
        env["HOME"] = str(isolated_home)
        env.pop("CLAUDE_CONFIG_DIR", None)

        jq_shim_dir = tmp_path / "jq-shim"
        jq_shim_dir.mkdir()
        jq_shim = jq_shim_dir / "jq"
        jq_shim.write_text(textwrap.dedent("""\
            #!/usr/bin/env python3
            # Test-only override of jq's real split/map behavior for
            # review-pr-checkout.sh's FILES_JSON encoding step -- always
            # emits a JSON array of non-string elements regardless of
            # stdin/argv, so audit-execution-surface.py's own "must be an
            # array of strings" rejection (otherwise unreachable through
            # this script's real pipeline) fires on real, self-fetched
            # input.
            import sys
            print("[1, 2, 3]")
            sys.exit(0)
        """))
        jq_shim.chmod(0o755)
        env["PATH"] = os.pathsep.join([str(jq_shim_dir), env["PATH"]])

        result = subprocess.run(
            ["bash", str(SCRIPT), PR_IDENTITY], cwd=repo, env=env, capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "rejected its own self-fetched file list as malformed input" in result.stderr
        assert not _worktree_dir(repo).exists()


class TestGhHostStripped:
    """Same reasoning as review-pr-post.sh's own test of this property:
    adversarial PR content could induce the calling agent to set GH_HOST
    ambiently, which would otherwise silently redirect a fact this script is
    supposed to be deriving independently to an attacker-chosen host."""

    def test_ambient_gh_host_does_not_reach_any_gh_invocation(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"],
            extra_env={"GH_HOST": "attacker-chosen-host.example", "GH_ENTERPRISE_TOKEN": "leaked-token"},
        )
        assert result.returncode == 0, result.stderr

        records = _read_records(call_log)
        assert len(records) >= 2
        for record in records:
            assert record["GH_HOST"] is None
            assert record["GH_ENTERPRISE_TOKEN"] is None


class TestSymlinkDetection:
    """audit-execution-surface.py's _classify() matches on path text alone,
    so it is blind to a git-tracked symlink (tree-entry mode 120000) with an
    innocuous-looking name -- review-pr-checkout.sh must catch it itself via
    `git ls-tree`'s own mode field, since git checks out a symlink verbatim
    and a Read tool would then transparently follow it outside the repo."""

    def test_pr_tracked_symlink_trips_the_stop_condition(self, isolated_home, tmp_path):
        _install_audit_script(isolated_home)
        repo, pr_sha = _build_repo_with_pr_ref(tmp_path, symlink_name="notes.txt")
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["pr_file.txt", "notes.txt"],
        )
        assert result.returncode != 0
        assert "notes.txt" in result.stderr
        assert "symlink" in result.stderr
        assert not _worktree_dir(repo).exists()

    def test_pre_existing_symlink_on_base_branch_untouched_by_this_pr_does_not_stop(
        self, isolated_home, tmp_path
    ):
        """The scoping guarantee: `legacy-symlink` is committed on the base
        branch and carried unchanged into the PR commit's own tree, so it is
        present at FETCHED_SHA -- but the PR's own changed-file list (what
        `files=` below reports, matching what gh's own files endpoint would
        report) never names it. A symlink check scoped to the whole tree
        would wrongly stop this review; scoped to the PR's own changed
        files, it must not, since this PR's own diff never touches it."""
        _install_audit_script(isolated_home)
        repo, pr_sha = _build_repo_with_pr_ref(tmp_path, base_symlink_name="legacy-symlink")
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["pr_file.txt"],
        )
        assert result.returncode == 0, result.stderr
        assert Path(result.stdout.strip()) == _worktree_dir(repo)
        # The pre-existing symlink really is present at the checked-out
        # commit -- proving this test's own premise, not merely that
        # checkout succeeded for an unrelated reason.
        worktree_dir = Path(result.stdout.strip())
        assert (worktree_dir / "legacy-symlink").is_symlink()

    def test_pathspec_magic_prefixed_symlink_name_still_trips_the_stop_condition(
        self, isolated_home, tmp_path
    ):
        """Regression test for the bypass `--literal-pathspecs` on the
        `git ls-tree` call closes: a changed-file path containing pathspec
        magic characters (a leading ':') must be matched literally, not
        reinterpreted as a glob or magic pathspec -- without the flag,
        `git ls-tree -r <tree> -- ':weird-colon-symlink'` returns zero output
        for this real tracked symlink, silently evading the check."""
        _install_audit_script(isolated_home)
        symlink_name = ":weird-colon-symlink"
        repo, pr_sha = _build_repo_with_pr_ref(tmp_path, symlink_name=symlink_name)
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["pr_file.txt", symlink_name],
        )
        assert result.returncode != 0
        assert symlink_name in result.stderr
        assert "symlink" in result.stderr
        assert not _worktree_dir(repo).exists()


class TestWorktreeReplaceIntegration:
    """The worktree lock/replace sequence's own concurrency properties
    (mutual exclusion, automatic release on a SIGKILLed holder, deadline
    exceeded) are unit-tested directly against review-pr-worktree-
    replace.py in test_review_pr_worktree_replace.py -- this only pins that
    review-pr-checkout.sh actually delegates to it and reacts sensibly to a
    transient contention, rather than re-testing the lock primitive itself
    through this script's much heavier subprocess-and-gh-shim harness."""

    def test_transient_lock_contention_is_waited_out_not_treated_as_failure(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        lock_path = Path(f"{_worktree_dir(repo)}.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        holder = subprocess.Popen(
            [
                sys.executable, "-c",
                "import fcntl, sys, time\n"
                "f = open(sys.argv[1], 'a+')\n"
                "fcntl.flock(f, fcntl.LOCK_EX)\n"
                "print('locked', flush=True)\n"
                "time.sleep(2)\n",
                str(lock_path),
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert holder.stdout.readline().strip() == "locked"
            result, _ = _run(
                repo, isolated_home, [PR_IDENTITY], tmp_path,
                head_ref_oid=pr_sha, files=["a.py"],
            )
            assert result.returncode == 0, result.stderr
            assert Path(result.stdout.strip()) == _worktree_dir(repo)
        finally:
            holder.wait(timeout=10)
