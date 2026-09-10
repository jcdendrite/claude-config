"""Tests for review-pr-post.sh.

The `gh` CLI is replaced by a PATH shim that records every invocation it
receives (one JSON object per line) so tests can assert on call history --
not just the script's exit code -- for the fail-closed paths' most
important property: `gh` is never even invoked.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest
from helpers import (
    SCRIPTS_DIR,
    head_sha,
    review_pr_completion_marker_path,
    write_review_pr_completion_marker,
)

from .conftest import _shimmed_env

SCRIPT = SCRIPTS_DIR / "review-pr-post.sh"
SID = "test-session-review-pr-post"
PR_IDENTITY = "foo/bar#42"


def _seed_session(home: Path, session_id: str, pid: int | None = None) -> None:
    """Write $HOME/.claude/sessions/<pid> in the two-line format
    capture-session-id.sh writes. Duplicated from
    hooks/tests/conftest.py's helper of the same name rather than
    cross-imported -- that file documents the same neither-test-tree-
    imports-the-other convention this mirrors.

    pid defaults to this test process's own pid: marker.sh (invoked by
    review-pr-post.sh) resolves its session id by walking process
    ancestors, and when it runs as a subprocess of pytest, that walk
    reaches the pytest process itself.
    """
    target_pid = os.getpid() if pid is None else pid
    sessions_dir = home / ".claude" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    start_time = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(target_pid)],
        env={**os.environ, "TZ": "UTC", "LC_ALL": "C"},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.rstrip("\n")
    (sessions_dir / str(target_pid)).write_text(f"{session_id}\n{start_time}\n")


@pytest.fixture
def isolated_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    return home


@pytest.fixture
def git_repo(tmp_path):
    """Fresh git repo with one committed file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "file.txt").write_text("first\n")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def _findings_body_path(home: Path, session_id: str = SID) -> Path:
    return home / ".claude" / ".review-pr-active.d" / f"{session_id}.body"


def _write_findings_body(home: Path, content: str = "# findings\n", session_id: str = SID) -> tuple[Path, str]:
    body = _findings_body_path(home, session_id)
    body.parent.mkdir(parents=True, exist_ok=True)
    body.write_text(content)
    return body, hashlib.sha256(body.read_bytes()).hexdigest()


def _write_marker(home: Path, repo: Path, head_ref_oid: str, body_hash: str, pr_identity: str = PR_IDENTITY) -> None:
    write_review_pr_completion_marker(home, repo, pr_identity, head_ref_oid, body_hash, SID)


def _gh_shim_source(
    call_log: Path, pr_view_head_ref_oid: str | None = None, fail_pr_review: bool = False
) -> str:
    """gh shim recording every invocation. Records GH_HOST/GH_ENTERPRISE_TOKEN
    as this shim process actually saw them (not as the test's own subprocess
    env set them), so a test can prove the script's `env -u` strip reached
    the real gh invocation rather than just asserting on the args list.

    `pr_view_head_ref_oid`, when given, is printed as the `gh pr view
    ... --json headRefOid --jq .headRefOid` call's stdout -- the script's
    PR-identity cross-check reads this to compare against the completion
    marker's recorded HEAD. Left unset, the shim prints nothing for that
    call, matching a `gh` failure or a PR whose current headRefOid the
    script cannot determine.

    `fail_pr_review`, when true, exits 1 on a `gh pr review` invocation
    specifically (every other invocation, including `pr view`, still exits
    0) -- the production `gh` binary's own always-0 stand-in otherwise never
    exercises the script's failure branch that leaves the completion marker
    intact for a retry."""
    pr_view_stdout = "" if pr_view_head_ref_oid is None else pr_view_head_ref_oid
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json
        import os
        import sys

        CALL_LOG = {str(call_log)!r}
        PR_VIEW_STDOUT = {pr_view_stdout!r}
        FAIL_PR_REVIEW = {fail_pr_review!r}
        args = sys.argv[1:]
        record = {{
            "args": args,
            "GH_HOST": os.environ.get("GH_HOST"),
            "GH_ENTERPRISE_TOKEN": os.environ.get("GH_ENTERPRISE_TOKEN"),
        }}
        with open(CALL_LOG, "a") as f:
            f.write(json.dumps(record) + chr(10))
        if args[:2] == ["pr", "view"] and PR_VIEW_STDOUT:
            print(PR_VIEW_STDOUT)
        if FAIL_PR_REVIEW and args[:2] == ["pr", "review"]:
            sys.exit(1)
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
    extra_env: dict | None = None,
    pr_view_head_ref_oid: str | None = None,
    fail_pr_review: bool = False,
) -> tuple[subprocess.CompletedProcess, Path]:
    call_log = tmp_path / "gh_calls.jsonl"
    env = {
        **_shimmed_env(
            tmp_path, _gh_shim_source(call_log, pr_view_head_ref_oid, fail_pr_review)
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


def _read_pr_review_calls(call_log: Path) -> list[list[str]]:
    """Calls whose args start with the `pr review` verb -- filters out the
    PR-identity cross-check's own `gh pr view` call, which every test below
    that reaches the posting calls now also triggers."""
    return [args for args in _read_calls(call_log) if args[:2] == ["pr", "review"]]


def _strip_comment_lines(text: str) -> str:
    """Drop full-line `#` comments -- this script has no inline trailing
    comments -- so a check for a code-level pattern doesn't false-positive
    on prose that merely discusses it (e.g. this script's own header
    explaining that --approve is unreachable)."""
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("#"))


class TestApproveIsNotReachable:
    """Confirms the property by construction, not just by behavior:
    grepping the script's own code (comments stripped) rather than only
    exercising it, so a future edit that reintroduces an `--approve`
    invocation anywhere in the file fails this test even if no test case
    happens to exercise that exact path."""

    def test_approve_flag_absent_from_code(self):
        code = _strip_comment_lines(SCRIPT.read_text())
        assert "approve" not in code.lower()

    def test_exactly_two_gh_pr_review_invocations_exist_in_code(self):
        """Scoped to the actual invocation prefix, not the bare substring
        'gh pr review' -- the usage() heredoc text also names the command
        in prose, which is not an invocation."""
        code = _strip_comment_lines(SCRIPT.read_text())
        assert code.count('gh pr review "$PR_NUMBER"') == 2


class TestUsageErrors:
    @pytest.mark.parametrize(
        "args",
        [[], ["comment", "extra"], ["approve"], ["--approve"], ["request-changes", "comment"]],
    )
    def test_invalid_argv_exits_two_with_no_gh_call(self, isolated_home, git_repo, tmp_path, args):
        result, call_log = _run(git_repo, isolated_home, args, tmp_path)
        assert result.returncode == 2
        assert "Usage" in result.stderr
        assert _read_calls(call_log) == []


class TestMissingCompletionMarker:
    def test_no_marker_at_all_fails_closed(self, isolated_home, git_repo, tmp_path):
        _seed_session(isolated_home, SID)
        result, call_log = _run(git_repo, isolated_home, ["comment"], tmp_path)
        assert result.returncode != 0
        assert "completion marker" in result.stderr
        assert _read_calls(call_log) == []


class TestHeadMismatch:
    def test_stale_head_ref_oid_fails_closed(self, isolated_home, git_repo, tmp_path):
        """A mid-review push moved HEAD after the marker was written -- the
        marker's stored headRefOid no longer names the tree about to be
        posted against."""
        _seed_session(isolated_home, SID)
        body_file, body_hash = _write_findings_body(isolated_home)
        _write_marker(isolated_home, git_repo, "0" * 40, body_hash)
        result, call_log = _run(git_repo, isolated_home, ["comment"], tmp_path)
        assert result.returncode != 0
        assert "HEAD" in result.stderr
        assert _read_calls(call_log) == []


class TestBodyHashMismatch:
    def test_findings_body_content_changed_since_review_fails_closed(
        self, isolated_home, git_repo, tmp_path
    ):
        _seed_session(isolated_home, SID)
        _write_findings_body(isolated_home, content="# changed after review\n")
        _write_marker(isolated_home, git_repo, head_sha(git_repo), "a" * 64)
        result, call_log = _run(git_repo, isolated_home, ["comment"], tmp_path)
        assert result.returncode != 0
        assert "hash" in result.stderr.lower()
        assert _read_calls(call_log) == []

    def test_findings_body_missing_fails_closed(self, isolated_home, git_repo, tmp_path):
        _seed_session(isolated_home, SID)
        _write_marker(isolated_home, git_repo, head_sha(git_repo), "a" * 64)
        result, call_log = _run(git_repo, isolated_home, ["comment"], tmp_path)
        assert result.returncode != 0
        assert _read_calls(call_log) == []

    def test_symlinked_findings_body_fails_closed(self, isolated_home, git_repo, tmp_path):
        """O_NOFOLLOW read: a pre-planted symlink at the fixed findings-body
        path must not be followed and hashed, the same TOCTOU class already
        closed for marker.sh's own read of this file."""
        _seed_session(isolated_home, SID)
        real_target = tmp_path / "attacker-chosen-target.md"
        real_target.write_text("# attacker-chosen content\n")
        body_path = _findings_body_path(isolated_home)
        body_path.parent.mkdir(parents=True, exist_ok=True)
        body_path.symlink_to(real_target)
        body_hash = hashlib.sha256(real_target.read_bytes()).hexdigest()
        _write_marker(isolated_home, git_repo, head_sha(git_repo), body_hash)
        result, call_log = _run(git_repo, isolated_home, ["comment"], tmp_path)
        assert result.returncode != 0
        assert _read_calls(call_log) == []


class TestMalformedPrIdentity:
    @pytest.mark.parametrize(
        "pr_identity",
        [
            "no-hash-or-slash",
            "foo/bar#NOTANUMBER",
            "#42",
            # A segment made entirely of '.'/'-' characters is a valid,
            # nonempty run under a naive [A-Za-z0-9._-]+ class, and turns a
            # `repos/$OWNER_REPO/...` gh call into a path-traversal shape --
            # must be rejected by the owner/repo regex before any gh call,
            # the same as the other malformed-identity shapes above.
            "../..#5",
        ],
    )
    def test_unparseable_identity_fails_closed(self, isolated_home, git_repo, tmp_path, pr_identity):
        _seed_session(isolated_home, SID)
        _, body_hash = _write_findings_body(isolated_home)
        _write_marker(isolated_home, git_repo, head_sha(git_repo), body_hash, pr_identity=pr_identity)
        result, call_log = _run(git_repo, isolated_home, ["comment"], tmp_path)
        assert result.returncode != 0
        assert _read_calls(call_log) == []


class TestOwnerRepoRegexAcceptsDotAndHyphenAlongsideAlnum:
    def test_owner_repo_with_dot_and_hyphen_segments_passes_regex_check(
        self, isolated_home, git_repo, tmp_path
    ):
        """Bounds the tightened owner/repo regex from the other side of
        TestMalformedPrIdentity's dot-only-segment deny case: a segment
        mixing '.'/'-' with at least one alphanumeric character (an
        ordinary GitHub owner/repo shape) must still pass. The shim below
        answers no `gh pr view` headRefOid, so the run still fails closed
        at the PR-identity cross-check further down -- the assertion below
        targets that later, different failure (a `gh pr view` call actually
        happened, and the error names headRefOid rather than an invalid
        owner/repo) to isolate the regex check from that downstream
        behavior."""
        _seed_session(isolated_home, SID)
        _, body_hash = _write_findings_body(isolated_home)
        _write_marker(
            isolated_home, git_repo, head_sha(git_repo), body_hash, pr_identity="my-org/my.repo#5"
        )
        result, call_log = _run(git_repo, isolated_home, ["comment"], tmp_path)
        assert result.returncode != 0
        assert "does not name a valid owner/repo" not in result.stderr
        assert "headRefOid" in result.stderr
        assert _read_calls(call_log) != []


class TestPrIdentityCrossCheck:
    """PR_NUMBER/OWNER_REPO are validated by regex shape alone before this
    check -- neither proves the marker's PR identity actually names the PR
    the marker's HEAD/body-hash checks ran against. This class pins the
    `gh pr view` re-fetch that closes that gap."""

    def test_pr_view_head_mismatch_fails_closed(self, isolated_home, git_repo, tmp_path):
        """The PR's own current headRefOid disagrees with the completion
        marker's recorded HEAD -- PR_NUMBER/OWNER_REPO does not name the
        reviewed PR. Must abort before ever calling `gh pr review`."""
        _seed_session(isolated_home, SID)
        _, body_hash = _write_findings_body(isolated_home)
        _write_marker(isolated_home, git_repo, head_sha(git_repo), body_hash)
        result, call_log = _run(
            git_repo,
            isolated_home,
            ["comment"],
            tmp_path,
            pr_view_head_ref_oid="f" * 40,
        )
        assert result.returncode != 0
        assert "headRefOid" in result.stderr
        assert _read_pr_review_calls(call_log) == []

    def test_pr_view_failure_fails_closed(self, isolated_home, git_repo, tmp_path):
        """`gh pr view` itself fails or returns no output -- treated the
        same as a mismatch, not as a pass-through."""
        _seed_session(isolated_home, SID)
        _, body_hash = _write_findings_body(isolated_home)
        _write_marker(isolated_home, git_repo, head_sha(git_repo), body_hash)
        result, call_log = _run(git_repo, isolated_home, ["comment"], tmp_path)
        assert result.returncode != 0
        assert "headRefOid" in result.stderr
        assert _read_pr_review_calls(call_log) == []


class TestHappyPath:
    @pytest.mark.parametrize("verdict,flag", [("comment", "--comment"), ("request-changes", "--request-changes")])
    def test_matching_marker_posts_exactly_once_with_the_named_verdict(
        self, isolated_home, git_repo, tmp_path, verdict, flag
    ):
        _seed_session(isolated_home, SID)
        body_file, body_hash = _write_findings_body(isolated_home)
        marker_head = head_sha(git_repo)
        _write_marker(isolated_home, git_repo, marker_head, body_hash)
        result, call_log = _run(
            git_repo, isolated_home, [verdict], tmp_path, pr_view_head_ref_oid=marker_head
        )
        assert result.returncode == 0, result.stderr

        calls = _read_pr_review_calls(call_log)
        assert len(calls) == 1
        args = calls[0]
        assert args[:2] == ["pr", "review"]
        assert args[2] == "42"
        assert flag in args
        assert "--approve" not in args
        r_index = args.index("-R")
        assert args[r_index + 1] == "foo/bar"
        f_index = args.index("-F")
        assert args[f_index + 1] == str(body_file)


class TestGhHostStripped:
    """GH_HOST/GH_ENTERPRISE_TOKEN must never reach the real `gh pr review`
    call: adversarial PR content could induce the calling agent to set
    GH_HOST ambiently, which would otherwise silently redirect the post to
    an attacker-chosen host before this script's own checks have any say."""

    def test_ambient_gh_host_does_not_reach_the_gh_invocation(
        self, isolated_home, git_repo, tmp_path
    ):
        _seed_session(isolated_home, SID)
        _, body_hash = _write_findings_body(isolated_home)
        marker_head = head_sha(git_repo)
        _write_marker(isolated_home, git_repo, marker_head, body_hash)
        result, call_log = _run(
            git_repo,
            isolated_home,
            ["comment"],
            tmp_path,
            extra_env={
                "GH_HOST": "attacker-chosen-host.example",
                "GH_ENTERPRISE_TOKEN": "leaked-token",
            },
            pr_view_head_ref_oid=marker_head,
        )
        assert result.returncode == 0, result.stderr

        # The gh pr view identity cross-check is a `gh` invocation too, so
        # GH_HOST/GH_ENTERPRISE_TOKEN must not leak into it either -- every
        # record in the log is asserted, not just the pr review one.
        records = _read_records(call_log)
        assert len(records) >= 1
        for record in records:
            assert record["GH_HOST"] is None, (
                "GH_HOST leaked into a gh invocation's effective environment"
            )
            assert record["GH_ENTERPRISE_TOKEN"] is None, (
                "GH_ENTERPRISE_TOKEN leaked into a gh invocation's effective environment"
            )


class TestCompletionMarkerSelfConsuming:
    """A gh pr review POST has no idempotency key, so the completion marker
    that authorizes it must be invalidated immediately after a successful
    post -- a retry must fail closed rather than double-post."""

    def test_second_invocation_fails_closed_after_the_first_succeeds(
        self, isolated_home, git_repo, tmp_path
    ):
        _seed_session(isolated_home, SID)
        _, body_hash = _write_findings_body(isolated_home)
        marker_head = head_sha(git_repo)
        _write_marker(isolated_home, git_repo, marker_head, body_hash)
        marker = review_pr_completion_marker_path(isolated_home, git_repo, SID)

        first, call_log = _run(
            git_repo, isolated_home, ["comment"], tmp_path, pr_view_head_ref_oid=marker_head
        )
        assert first.returncode == 0, first.stderr
        assert len(_read_pr_review_calls(call_log)) == 1
        assert not marker.exists(), (
            "a successful post must delete the completion marker it consumed"
        )

        second, call_log = _run(
            git_repo, isolated_home, ["comment"], tmp_path, pr_view_head_ref_oid=marker_head
        )
        assert second.returncode != 0
        assert "completion marker" in second.stderr
        assert len(_read_pr_review_calls(call_log)) == 1, (
            "a retry after a successful post must not call gh pr review again "
            "-- the one recorded call must still be the first invocation's"
        )


class TestGhPrReviewFailureBranch:
    """The `gh` test shim otherwise always exits 0, so without this class
    the script's `if ! ... gh pr review ...; then ... exit 2` failure
    branches are never exercised -- a regression making completion-marker
    deletion unconditional (deleting it even when the post failed, breaking
    retry-safety) would pass every other test in this file."""

    @pytest.mark.parametrize("verdict", ["comment", "request-changes"])
    def test_gh_pr_review_failure_exits_nonzero_and_keeps_the_marker(
        self, isolated_home, git_repo, tmp_path, verdict
    ):
        _seed_session(isolated_home, SID)
        _, body_hash = _write_findings_body(isolated_home)
        marker_head = head_sha(git_repo)
        _write_marker(isolated_home, git_repo, marker_head, body_hash)
        marker = review_pr_completion_marker_path(isolated_home, git_repo, SID)

        result, call_log = _run(
            git_repo,
            isolated_home,
            [verdict],
            tmp_path,
            pr_view_head_ref_oid=marker_head,
            fail_pr_review=True,
        )
        assert result.returncode != 0
        assert len(_read_pr_review_calls(call_log)) == 1, (
            "the failing gh pr review call must still have been attempted"
        )
        assert marker.exists(), (
            "a failed post must leave the completion marker intact for a retry"
        )
