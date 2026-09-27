"""Tests for review-pr-diff.sh -- the no-checkout review path for a
restricted (cross-repo or first-time-contributor) PR that
review-pr-checkout.sh's trust block refuses. Mirrors
review-pr-checkout.sh's self-derivation discipline (own PR-identity parse,
own origin-identity check, own paginated file list, own double headRefOid
fetch) minus everything that only matters once code lands on disk.

The `gh` CLI is replaced by a PATH shim that records every invocation it
receives, matching test_review_pr_checkout.py's own shim pattern -- git is
left real (not shimmed).
"""
from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest
from helpers import SCRIPTS_DIR

from .conftest import _build_repo_with_pr_ref, _install_audit_script, _seed_session, _shimmed_env

SCRIPT = SCRIPTS_DIR / "review-pr-diff.sh"
OWNER_REPO = "foo/bar"
PR_NUMBER = "42"
PR_IDENTITY = f"{OWNER_REPO}#{PR_NUMBER}"
SID = "test-session-review-pr-diff"


@pytest.fixture
def isolated_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    return home


@pytest.fixture
def repo_with_pr_ref(tmp_path):
    return _build_repo_with_pr_ref(tmp_path, owner_repo=OWNER_REPO, pr_number=PR_NUMBER)


def _diff_text_for(files: list[str]) -> str:
    """A minimal but structurally valid unified diff: one `diff --git`
    header per file, matching the header-count truncation heuristic the
    script checks the paginated file count against."""
    parts = []
    for f in files:
        parts.append(f"diff --git a/{f} b/{f}\nindex 000..111 100644\n--- a/{f}\n+++ b/{f}\n@@ -0,0 +1 @@\n+x\n")
    return "".join(parts)


def _gh_shim_source(
    call_log: Path,
    head_ref_oid: str | None = None,
    files: list[str] | None = None,
    diff_text: str | None = None,
    fail_pr_view: bool = False,
    fail_files: bool = False,
    head_ref_oid_second: str | None = None,
    fail_diff: bool = False,
    diff_error_text: str | None = None,
) -> str:
    """gh shim recording every invocation, matching
    test_review_pr_checkout.py's own shim shape. `diff_text` defaults to a
    structurally valid diff matching `files` (one `diff --git` header per
    file) unless explicitly overridden. `fail_diff` exits 1 on the `gh pr
    diff` call, printing `diff_error_text` to stderr if given -- models a
    `gh` failure whose stderr could echo request parameters, which the
    script must never capture into the `.diff` file or its own output."""
    resolved_diff_text = diff_text if diff_text is not None else _diff_text_for(files or [])
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json
        import os
        import sys

        CALL_LOG = {str(call_log)!r}
        HEAD_REF_OID = {head_ref_oid!r}
        HEAD_REF_OID_SECOND = {head_ref_oid_second!r}
        FILES = {list(files or [])!r}
        DIFF_TEXT = {resolved_diff_text!r}
        FAIL_PR_VIEW = {fail_pr_view!r}
        FAIL_FILES = {fail_files!r}
        FAIL_DIFF = {fail_diff!r}
        DIFF_ERROR_TEXT = {diff_error_text!r}
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
        if args[:2] == ["pr", "view"]:
            if FAIL_PR_VIEW:
                sys.exit(1)
            oid = HEAD_REF_OID
            if HEAD_REF_OID_SECOND is not None and prior_pr_view_calls >= 1:
                oid = HEAD_REF_OID_SECOND
            if oid:
                print(oid)
            sys.exit(0)
        if args[:2] == ["pr", "diff"]:
            if FAIL_DIFF:
                if DIFF_ERROR_TEXT:
                    print(DIFF_ERROR_TEXT, file=sys.stderr)
                sys.exit(1)
            sys.stdout.write(DIFF_TEXT)
            sys.exit(0)
        if args[:1] == ["api"]:
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


def _local_pr_ref_names(repo: Path) -> str:
    return subprocess.run(
        ["git", "for-each-ref", "refs/review-pr"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


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
) -> tuple[subprocess.CompletedProcess, Path]:
    _seed_session(home, SID)
    call_log = tmp_path / "gh_calls.jsonl"
    env = {
        **_shimmed_env(
            tmp_path,
            _gh_shim_source(
                call_log, head_ref_oid, files, diff_text, fail_pr_view, fail_files,
                head_ref_oid_second, fail_diff, diff_error_text,
            ),
        ),
        "HOME": str(home),
    }
    env.pop("CLAUDE_CONFIG_DIR", None)
    result = subprocess.run(
        ["bash", str(SCRIPT), *args], cwd=cwd, env=env, capture_output=True, text=True,
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
        assert result.returncode != 0
        assert "origin" in result.stderr
        assert _read_calls(call_log) == []


class TestMissingAuditScript:
    def test_uninstalled_skill_directory_aborts_before_any_fetch(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"]
        )
        assert result.returncode != 0
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
        assert result.returncode != 0
        assert "force-push" in result.stderr
        calls = _read_calls(call_log)
        assert not any(c[:2] == ["pr", "diff"] for c in calls), (
            "a drift-detected abort must never reach the diff fetch"
        )


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

        diff_path = Path(result.stdout.strip())
        assert diff_path == isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.diff"
        assert diff_path.exists()
        assert "diff --git a/a.py b/a.py" in diff_path.read_text()

        provenance = isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.provenance"
        lines = provenance.read_text().splitlines()
        assert lines[0] == PR_IDENTITY
        assert lines[1] == pr_sha
        assert lines[2].isdigit()
        assert lines[3] == "diff-only"

        assert _local_pr_ref_names(repo) == "", "diff-only mode must never fetch a local PR ref"
        worktree_dir = repo / ".claude" / "worktrees" / f"review-pr-{OWNER_REPO.replace('/', '-')}-{PR_NUMBER}"
        assert not worktree_dir.exists()
        assert not Path(f"{worktree_dir}.lock").exists()


class TestAuditHitReportedNotFatal:
    def test_execution_surface_hit_is_reported_on_stderr_but_exits_zero(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """Distinct from review-pr-checkout.sh's own audit stop: nothing
        lands on disk here, so the audit predicate has no subject to
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
        diff_path = Path(result.stdout.strip())
        assert diff_path.exists()


class TestDiffTruncationHeuristic:
    def test_diff_header_count_mismatch_is_reported_not_fatal(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """Not a vendor-documented size limit -- a structural completeness
        check (one `diff --git` header per changed file) against a
        possibly-truncated `gh pr diff` response."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py", "b.py"], diff_text=_diff_text_for(["a.py"]),
        )
        assert result.returncode == 0, result.stderr
        assert "truncated" in result.stderr


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
        secret_shaped_text = "leaked-token=ghp_AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHHIIII"
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], fail_diff=True, diff_error_text=secret_shaped_text,
        )
        assert result.returncode != 0
        assert secret_shaped_text not in result.stdout
        assert secret_shaped_text not in result.stderr
        diff_path = isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.diff"
        assert not diff_path.exists()
