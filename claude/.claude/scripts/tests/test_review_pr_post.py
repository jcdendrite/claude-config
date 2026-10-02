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

from .conftest import _seed_session, _shimmed_env

SCRIPT = SCRIPTS_DIR / "review-pr-post.sh"
SID = "test-session-review-pr-post"
OWNER_REPO = "foo/bar"
PR_IDENTITY = f"{OWNER_REPO}#42"

# A harness bound so a hung bash or interpreter fails one test instead of the suite.
_SUBPROCESS_TIMEOUT_SECONDS = 60


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
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS)
    (repo / "file.txt").write_text("first\n")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS)
    subprocess.run(
        ["git", "remote", "add", "origin", f"https://github.com/{OWNER_REPO}.git"],
        cwd=repo, check=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    )
    return repo


def _findings_body_path(home: Path, session_id: str = SID) -> Path:
    return home / ".claude" / ".review-pr-active.d" / f"{session_id}.body"


def _write_findings_body(
    home: Path, content: str | bytes = "# findings\n", session_id: str = SID
) -> tuple[Path, str]:
    body = _findings_body_path(home, session_id)
    body.parent.mkdir(parents=True, exist_ok=True)
    body.write_bytes(content if isinstance(content, bytes) else content.encode())
    return body, hashlib.sha256(body.read_bytes()).hexdigest()


def _write_marker(home: Path, repo: Path, head_ref_oid: str, body_hash: str, pr_identity: str = PR_IDENTITY) -> None:
    write_review_pr_completion_marker(home, repo, pr_identity, head_ref_oid, body_hash, SID)


def _gh_shim_source(
    call_log: Path,
    pr_view_head_ref_oid: str | None = None,
    pr_review_exit_status: int = 0,
    marker_path: Path | None = None,
    swap_body_path: Path | None = None,
    swap_body_content: str | None = None,
    swap_body_symlink_target: Path | None = None,
    remove_marker_path: Path | None = None,
) -> str:
    """gh shim recording every invocation.

    `pr_view_head_ref_oid`, when given, is printed as the `gh pr view
    ... --json headRefOid --jq .headRefOid` call's stdout -- the script's
    PR-identity cross-check reads this to compare against the completion
    marker's recorded HEAD. Left unset, the shim prints nothing for that
    call, matching a `gh` failure or a PR whose current headRefOid the
    script cannot determine.

    `pr_review_exit_status`, when nonzero, is the exit status of a `gh pr
    review` invocation specifically (every other invocation, including `pr
    view`, still exits 0) -- the shim's own always-0 default otherwise never
    exercises the script's post-failure branch. 124 and 143 model
    timeout(1) killing a `gh` that outlived its cap.

    A `gh pr review` call records the body bytes it was handed, read from
    stdin for `-F -` (as real gh does) or from the named file otherwise, and
    whether `marker_path` still existed when it was called.

    `swap_body_path` names a findings-body file the shim replaces during
    the `gh pr view` call -- with `swap_body_content`, or with a symlink to
    `swap_body_symlink_target` -- modeling a writer racing the window between
    the script's read of that file and its post.

    `remove_marker_path` names a completion marker the shim deletes during
    the `gh pr view` call, modeling a second invocation consuming the marker
    in the window between this script's read of it and its own removal."""
    pr_view_stdout = "" if pr_view_head_ref_oid is None else pr_view_head_ref_oid
    marker_path_text = None if marker_path is None else str(marker_path)
    remove_marker_path_text = None if remove_marker_path is None else str(remove_marker_path)
    swap_body_path_text = None if swap_body_path is None else str(swap_body_path)
    swap_body_symlink_target_text = None if swap_body_symlink_target is None else str(swap_body_symlink_target)
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json
        import os
        import sys

        CALL_LOG = {str(call_log)!r}
        PR_VIEW_STDOUT = {pr_view_stdout!r}
        PR_REVIEW_EXIT_STATUS = {pr_review_exit_status!r}
        MARKER_PATH = {marker_path_text!r}
        REMOVE_MARKER_PATH = {remove_marker_path_text!r}
        SWAP_BODY_PATH = {swap_body_path_text!r}
        SWAP_BODY_CONTENT = {swap_body_content!r}
        SWAP_BODY_SYMLINK_TARGET = {swap_body_symlink_target_text!r}
        args = sys.argv[1:]
        record = {{
            "args": args,
        }}
        if args[:2] == ["pr", "review"]:
            record["marker_exists_at_post"] = None if MARKER_PATH is None else os.path.exists(MARKER_PATH)
            # Captured at invocation time so a test can assert what gh would
            # have posted, whatever the file holds when the script later exits.
            if "-F" in args:
                body_source = args[args.index("-F") + 1]
                if body_source == "-":
                    posted_bytes = sys.stdin.buffer.read()
                else:
                    try:
                        with open(body_source, "rb") as f:
                            posted_bytes = f.read()
                    except OSError:
                        posted_bytes = None
                record["posted_bytes_hex"] = None if posted_bytes is None else posted_bytes.hex()
        with open(CALL_LOG, "a") as f:
            f.write(json.dumps(record) + chr(10))
        if args[:2] == ["pr", "view"]:
            if REMOVE_MARKER_PATH is not None:
                os.unlink(REMOVE_MARKER_PATH)
            if SWAP_BODY_PATH is not None:
                os.unlink(SWAP_BODY_PATH)
                if SWAP_BODY_SYMLINK_TARGET is not None:
                    os.symlink(SWAP_BODY_SYMLINK_TARGET, SWAP_BODY_PATH)
                else:
                    with open(SWAP_BODY_PATH, "w") as f:
                        f.write(SWAP_BODY_CONTENT)
            if PR_VIEW_STDOUT:
                print(PR_VIEW_STDOUT)
        if args[:2] == ["pr", "review"]:
            sys.exit(PR_REVIEW_EXIT_STATUS)
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
    **shim_options,
) -> tuple[subprocess.CompletedProcess, Path]:
    call_log = tmp_path / "gh_calls.jsonl"
    env = {**_shimmed_env(tmp_path, _gh_shim_source(call_log, **shim_options)), "HOME": str(home)}
    env.pop("CLAUDE_CONFIG_DIR", None)
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


def _pr_review_records(call_log: Path) -> list[dict]:
    """Full records (args plus what the shim captured) of the `pr review` calls."""
    return [r for r in _read_records(call_log) if r["args"][:2] == ["pr", "review"]]


def _read_pr_review_calls(call_log: Path) -> list[list[str]]:
    """Calls whose args start with the `pr review` verb -- filters out the
    PR-identity cross-check's own `gh pr view` call, which every test that
    reaches the posting calls also triggers."""
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
        """Scoped to the actual invocation prefix (via _lib_gh, which
        prepends the capped `gh` wrapping -- see
        _lib.sh), not the bare substring 'gh pr review' -- the usage()
        heredoc text also names the command in prose, which is not an
        invocation."""
        code = _strip_comment_lines(SCRIPT.read_text())
        assert code.count('_lib_gh "$GH_PR_REVIEW_TIMEOUT_SECONDS" pr review "$PR_NUMBER"') == 2


class TestUsageErrors:
    @pytest.mark.parametrize(
        "args",
        [
            [],
            ["comment"],
            ["comment", PR_IDENTITY, "extra"],
            ["comment", "extra"],
            ["approve", PR_IDENTITY],
            ["--approve", PR_IDENTITY],
            ["request-changes", "comment"],
            ["comment", "foo/bar#NOTANUMBER"],
            ["comment", "../..#5"],
        ],
    )
    def test_invalid_argv_exits_two_with_no_gh_call(self, isolated_home, git_repo, tmp_path, args):
        result, call_log = _run(git_repo, isolated_home, args, tmp_path)
        assert result.returncode == 2
        assert "Usage" in result.stderr
        assert _read_calls(call_log) == []


class TestMissingCompletionMarker:
    def test_no_marker_at_all_fails_closed(self, isolated_home, git_repo, tmp_path):
        _seed_session(isolated_home, SID)
        result, call_log = _run(git_repo, isolated_home, ["comment", PR_IDENTITY], tmp_path)
        assert result.returncode != 0
        assert "completion marker" in result.stderr
        assert _read_calls(call_log) == []

    def test_another_sessions_marker_at_the_same_repo_hash_does_not_authorize_a_post(
        self, isolated_home, git_repo, tmp_path
    ):
        """Every session of a repo shares one repo-hash prefix, so the
        `.<session-id>` suffix is the only isolation between their markers.
        The other session's marker is otherwise valid for this session's
        body and for the remote head."""
        _seed_session(isolated_home, SID)
        _, body_hash = _write_findings_body(isolated_home)
        marker_head = head_sha(git_repo)
        other_sessions_marker = write_review_pr_completion_marker(
            isolated_home, git_repo, PR_IDENTITY, marker_head, body_hash, f"{SID}-other"
        )
        result, call_log = _run(
            git_repo, isolated_home, ["comment", PR_IDENTITY], tmp_path, pr_view_head_ref_oid=marker_head
        )
        assert result.returncode != 0
        assert "completion marker" in result.stderr
        assert _read_calls(call_log) == []
        assert other_sessions_marker.exists(), "another session's marker must not be consumed"


class TestLocalHeadIsNotConsulted:
    def test_checkout_mode_posts_when_only_the_remote_head_matches_the_marker(
        self, isolated_home, git_repo, tmp_path
    ):
        """The marker is keyed to the main tree root, so the current tree's
        HEAD need not be the reviewed commit. The remote headRefOid re-check
        (TestPrIdentityCrossCheck) is the freshness binding in checkout mode
        too."""
        _seed_session(isolated_home, SID)
        _, body_hash = _write_findings_body(isolated_home)
        reviewed_head = "0" * 40
        assert reviewed_head != head_sha(git_repo)
        _write_marker(isolated_home, git_repo, reviewed_head, body_hash)
        result, call_log = _run(
            git_repo, isolated_home, ["comment", PR_IDENTITY], tmp_path, pr_view_head_ref_oid=reviewed_head
        )
        assert result.returncode == 0, result.stderr
        assert len(_read_pr_review_calls(call_log)) == 1

    def test_marker_resolves_from_a_linked_worktree_of_the_repo(self, isolated_home, git_repo, tmp_path):
        """The marker path hashes the main tree's root, so running the post
        from any linked worktree finds the same marker."""
        _seed_session(isolated_home, SID)
        _, body_hash = _write_findings_body(isolated_home)
        marker_head = head_sha(git_repo)
        _write_marker(isolated_home, git_repo, marker_head, body_hash)
        linked_worktree = tmp_path / "linked-post-worktree"
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(linked_worktree)],
            cwd=git_repo, check=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
        result, call_log = _run(
            linked_worktree, isolated_home, ["comment", PR_IDENTITY], tmp_path, pr_view_head_ref_oid=marker_head
        )
        assert result.returncode == 0, result.stderr
        assert len(_read_pr_review_calls(call_log)) == 1


class TestBodyHashMismatch:
    def test_findings_body_content_changed_since_review_fails_closed(
        self, isolated_home, git_repo, tmp_path
    ):
        _seed_session(isolated_home, SID)
        _write_findings_body(isolated_home, content="# changed after review\n")
        _write_marker(isolated_home, git_repo, head_sha(git_repo), "a" * 64)
        result, call_log = _run(git_repo, isolated_home, ["comment", PR_IDENTITY], tmp_path)
        assert result.returncode != 0
        assert "hash" in result.stderr.lower()
        assert _read_calls(call_log) == []
        assert review_pr_completion_marker_path(isolated_home, git_repo, SID).exists()

    def test_findings_body_missing_fails_closed(self, isolated_home, git_repo, tmp_path):
        _seed_session(isolated_home, SID)
        _write_marker(isolated_home, git_repo, head_sha(git_repo), "a" * 64)
        result, call_log = _run(git_repo, isolated_home, ["comment", PR_IDENTITY], tmp_path)
        assert result.returncode != 0
        assert _read_calls(call_log) == []
        assert review_pr_completion_marker_path(isolated_home, git_repo, SID).exists()

    def test_findings_body_holding_a_nul_byte_fails_closed(self, isolated_home, git_repo, tmp_path):
        """bash cannot carry a NUL byte in the body it posts, so the hash of the
        bytes it holds differs from the marker's hash of the file, and the post
        refuses rather than sending altered text."""
        _seed_session(isolated_home, SID)
        _, body_hash = _write_findings_body(isolated_home, content=b"before\x00after\n")
        _write_marker(isolated_home, git_repo, head_sha(git_repo), body_hash)
        result, call_log = _run(git_repo, isolated_home, ["comment", PR_IDENTITY], tmp_path)
        assert result.returncode != 0
        assert "hash" in result.stderr.lower()
        assert _read_calls(call_log) == []
        assert review_pr_completion_marker_path(isolated_home, git_repo, SID).exists()

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
        result, call_log = _run(git_repo, isolated_home, ["comment", PR_IDENTITY], tmp_path)
        assert result.returncode != 0
        assert _read_calls(call_log) == []
        assert review_pr_completion_marker_path(isolated_home, git_repo, SID).exists()


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
        result, call_log = _run(git_repo, isolated_home, ["comment", PR_IDENTITY], tmp_path)
        assert result.returncode != 0
        assert "marker PR identity" in result.stderr, (
            "the marker's own malformed identity must be what refuses, not the target comparison"
        )
        assert _read_calls(call_log) == []


class TestOwnerRepoRegexAcceptsDotAndHyphenAlongsideAlnum:
    def test_owner_repo_with_dot_and_hyphen_segments_passes_regex_check(
        self, isolated_home, git_repo, tmp_path
    ):
        """Bounds the owner/repo regex from the other side of
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
        dotted_identity = "my-org/my.repo#5"
        subprocess.run(
            ["git", "remote", "set-url", "origin", "https://github.com/my-org/my.repo.git"],
            cwd=git_repo, check=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
        _write_marker(
            isolated_home, git_repo, head_sha(git_repo), body_hash, pr_identity=dotted_identity
        )
        result, call_log = _run(git_repo, isolated_home, ["comment", dotted_identity], tmp_path)
        assert result.returncode != 0
        assert "headRefOid" in result.stderr
        assert _read_calls(call_log) != []


class TestPrIdentityCrossCheck:
    """PR_NUMBER/OWNER_REPO are validated by regex shape alone before this
    check -- neither proves the marker's PR identity actually names the PR
    the marker's body-hash check ran against. This class pins the
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
            ["comment", PR_IDENTITY],
            tmp_path,
            pr_view_head_ref_oid="f" * 40,
        )
        assert result.returncode != 0
        assert "headRefOid" in result.stderr
        assert _read_pr_review_calls(call_log) == []
        assert review_pr_completion_marker_path(isolated_home, git_repo, SID).exists(), (
            "a refusal before the claim leaves the marker armed"
        )

    def test_pr_view_failure_fails_closed(self, isolated_home, git_repo, tmp_path):
        """`gh pr view` itself fails or returns no output -- treated the
        same as a mismatch, not as a pass-through."""
        _seed_session(isolated_home, SID)
        _, body_hash = _write_findings_body(isolated_home)
        _write_marker(isolated_home, git_repo, head_sha(git_repo), body_hash)
        result, call_log = _run(git_repo, isolated_home, ["comment", PR_IDENTITY], tmp_path)
        assert result.returncode != 0
        assert "headRefOid" in result.stderr
        assert _read_pr_review_calls(call_log) == []
        assert review_pr_completion_marker_path(isolated_home, git_repo, SID).exists()


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
            git_repo, isolated_home, [verdict, PR_IDENTITY], tmp_path, pr_view_head_ref_oid=marker_head
        )
        assert result.returncode == 0, result.stderr

        pr_review_records = [r for r in _read_records(call_log) if r["args"][:2] == ["pr", "review"]]
        assert len(pr_review_records) == 1
        record = pr_review_records[0]
        args = record["args"]
        assert args[:2] == ["pr", "review"]
        assert args[2] == "42"
        assert flag in args
        assert "--approve" not in args
        r_index = args.index("-R")
        assert args[r_index + 1] == "foo/bar"
        assert bytes.fromhex(record["posted_bytes_hex"]) == body_file.read_bytes()

    @pytest.mark.parametrize(
        "body",
        [
            "# findings\n",
            "no trailing newline",
            "trailing blank lines\n\n\n",
            "  leading space and a tab\t\n",
            "caf\u00e9 \u4e2d\u6587 \U0001f600\r\nsecond line\r\n",
            b"\xff\xfe not utf-8\n",
            "x" * 300_000 + "\n",
        ],
        ids=[
            "plain", "no-trailing-newline", "trailing-blank-lines", "leading-whitespace",
            "non-ascii-and-crlf", "non-utf8-bytes", "larger-than-a-pipe-buffer",
        ],
    )
    def test_posted_bytes_equal_the_file_bytes_the_marker_hashed(
        self, isolated_home, git_repo, tmp_path, body
    ):
        _seed_session(isolated_home, SID)
        body_file, body_hash = _write_findings_body(isolated_home, content=body)
        marker_head = head_sha(git_repo)
        _write_marker(isolated_home, git_repo, marker_head, body_hash)
        result, call_log = _run(
            git_repo, isolated_home, ["comment", PR_IDENTITY], tmp_path, pr_view_head_ref_oid=marker_head
        )
        assert result.returncode == 0, result.stderr
        (record,) = _pr_review_records(call_log)
        assert bytes.fromhex(record["posted_bytes_hex"]) == body_file.read_bytes()


class TestPostedBytesAreTheHashedBytes:
    """The body is read once, hashed, and handed to gh from that same read, so
    a writer replacing the file while the script waits on `gh pr view` cannot
    change what is posted."""

    @pytest.mark.parametrize("swap_kind", ["overwritten-content", "replaced-by-symlink"])
    def test_body_swapped_after_the_hash_check_is_not_what_gets_posted(
        self, isolated_home, git_repo, tmp_path, swap_kind
    ):
        _seed_session(isolated_home, SID)
        original_body = "# reviewed findings\n"
        body_file, body_hash = _write_findings_body(isolated_home, content=original_body)
        marker_head = head_sha(git_repo)
        _write_marker(isolated_home, git_repo, marker_head, body_hash)
        credential_file = tmp_path / "credential-file"
        credential_file.write_text("secret-shaped content\n")
        if swap_kind == "overwritten-content":
            swap_options = {"swap_body_content": "# unreviewed payload\n"}
        else:
            swap_options = {"swap_body_symlink_target": credential_file}

        result, call_log = _run(
            git_repo, isolated_home, ["comment", PR_IDENTITY], tmp_path,
            pr_view_head_ref_oid=marker_head, swap_body_path=body_file, **swap_options,
        )
        assert result.returncode == 0, result.stderr
        assert body_file.read_bytes() != original_body.encode(), "the shim must have swapped the file"
        (record,) = _pr_review_records(call_log)
        assert bytes.fromhex(record["posted_bytes_hex"]) == original_body.encode()


class TestModeGating:
    """Both known modes post once the remote headRefOid re-check
    (TestPrIdentityCrossCheck) passes; any other mode refuses."""

    def test_diff_only_mode_still_posts(
        self, isolated_home, git_repo, tmp_path
    ):
        _seed_session(isolated_home, SID)
        body_file, body_hash = _write_findings_body(isolated_home)
        remote_head = "f" * 40
        write_review_pr_completion_marker(
            isolated_home, git_repo, PR_IDENTITY, remote_head, body_hash, SID, mode="diff-only"
        )
        result, call_log = _run(
            git_repo, isolated_home, ["comment", PR_IDENTITY], tmp_path, pr_view_head_ref_oid=remote_head
        )
        assert result.returncode == 0, result.stderr
        assert len(_read_pr_review_calls(call_log)) == 1

    def test_diff_only_mode_still_refuses_on_remote_head_ref_oid_mismatch(
        self, isolated_home, git_repo, tmp_path
    ):
        _seed_session(isolated_home, SID)
        _, body_hash = _write_findings_body(isolated_home)
        write_review_pr_completion_marker(
            isolated_home, git_repo, PR_IDENTITY, "f" * 40, body_hash, SID, mode="diff-only"
        )
        result, call_log = _run(
            git_repo, isolated_home, ["comment", PR_IDENTITY], tmp_path, pr_view_head_ref_oid="0" * 40
        )
        assert result.returncode != 0
        assert "headRefOid" in result.stderr
        assert _read_pr_review_calls(call_log) == []

    def test_out_of_enum_mode_fails_closed(self, isolated_home, git_repo, tmp_path):
        """A corrupted or hand-written provenance/marker (any process that
        can write files can write this skill's own state) must refuse
        rather than fall through to either known branch by default."""
        _seed_session(isolated_home, SID)
        _, body_hash = _write_findings_body(isolated_home)
        write_review_pr_completion_marker(
            isolated_home, git_repo, PR_IDENTITY, head_sha(git_repo), body_hash, SID, mode="acquired"
        )
        result, call_log = _run(git_repo, isolated_home, ["comment", PR_IDENTITY], tmp_path)
        assert result.returncode != 0
        assert "neither checkout nor diff-only" in result.stderr
        assert _read_calls(call_log) == []


class TestCompletionMarkerSelfConsuming:
    """A gh pr review POST has no idempotency key, so the completion marker
    that authorizes it is consumed before the post call, so a retry finds no
    marker and is refused, and a marker that cannot be removed refuses before
    any post."""

    @pytest.mark.parametrize("verdict", ["comment", "request-changes"])
    def test_marker_is_already_consumed_when_gh_pr_review_is_called(
        self, isolated_home, git_repo, tmp_path, verdict
    ):
        _seed_session(isolated_home, SID)
        _, body_hash = _write_findings_body(isolated_home)
        marker_head = head_sha(git_repo)
        _write_marker(isolated_home, git_repo, marker_head, body_hash)
        marker = review_pr_completion_marker_path(isolated_home, git_repo, SID)
        assert marker.exists()

        result, call_log = _run(
            git_repo, isolated_home, [verdict, PR_IDENTITY], tmp_path,
            pr_view_head_ref_oid=marker_head, marker_path=marker,
        )
        assert result.returncode == 0, result.stderr
        (record,) = _pr_review_records(call_log)
        assert record["marker_exists_at_post"] is False, (
            "the marker must be gone before gh pr review runs, so a stop mid-call cannot leave it armed"
        )

    @pytest.mark.skipif(os.geteuid() == 0, reason="root can remove a marker from a read-only directory")
    def test_marker_that_cannot_be_removed_refuses_before_any_post(self, isolated_home, git_repo, tmp_path):
        _seed_session(isolated_home, SID)
        _, body_hash = _write_findings_body(isolated_home)
        marker_head = head_sha(git_repo)
        _write_marker(isolated_home, git_repo, marker_head, body_hash)
        marker = review_pr_completion_marker_path(isolated_home, git_repo, SID)
        marker.parent.chmod(0o500)
        try:
            result, call_log = _run(
                git_repo, isolated_home, ["comment", PR_IDENTITY], tmp_path, pr_view_head_ref_oid=marker_head
            )
            assert marker.exists()
        finally:
            marker.parent.chmod(0o700)
        assert result.returncode != 0
        assert "could not consume the completion marker" in result.stderr
        assert _read_pr_review_calls(call_log) == []

    def test_marker_consumed_by_a_racing_invocation_refuses_before_any_post(
        self, isolated_home, git_repo, tmp_path
    ):
        _seed_session(isolated_home, SID)
        _, body_hash = _write_findings_body(isolated_home)
        marker_head = head_sha(git_repo)
        _write_marker(isolated_home, git_repo, marker_head, body_hash)
        marker = review_pr_completion_marker_path(isolated_home, git_repo, SID)

        result, call_log = _run(
            git_repo, isolated_home, ["comment", PR_IDENTITY], tmp_path,
            pr_view_head_ref_oid=marker_head, remove_marker_path=marker,
        )
        assert result.returncode != 0
        assert "could not consume the completion marker" in result.stderr
        assert _read_pr_review_calls(call_log) == [], (
            "only the invocation that removes the marker may post; one that finds it already gone must not"
        )

    def test_second_invocation_fails_closed_after_the_first_succeeds(
        self, isolated_home, git_repo, tmp_path
    ):
        _seed_session(isolated_home, SID)
        _, body_hash = _write_findings_body(isolated_home)
        marker_head = head_sha(git_repo)
        _write_marker(isolated_home, git_repo, marker_head, body_hash)
        marker = review_pr_completion_marker_path(isolated_home, git_repo, SID)

        first, call_log = _run(
            git_repo, isolated_home, ["comment", PR_IDENTITY], tmp_path, pr_view_head_ref_oid=marker_head
        )
        assert first.returncode == 0, first.stderr
        assert len(_read_pr_review_calls(call_log)) == 1
        assert not marker.exists(), (
            "a successful post must delete the completion marker it consumed"
        )

        second, call_log = _run(
            git_repo, isolated_home, ["comment", PR_IDENTITY], tmp_path, pr_view_head_ref_oid=marker_head
        )
        assert second.returncode != 0
        assert "completion marker" in second.stderr
        assert len(_read_pr_review_calls(call_log)) == 1, (
            "a retry after a successful post must not call gh pr review again "
            "-- the one recorded call must still be the first invocation's"
        )


class TestPostFailureConsumesTheMarker:
    """A `gh pr review` POST has no idempotency key, so a failure -- including
    timeout(1) killing a `gh` that had already sent the request -- leaves
    unknown whether the review landed. Leaving the marker armed would let a
    retry double-post. The shim's default always-0 exit otherwise never
    exercises this branch."""

    @pytest.mark.parametrize("verdict", ["comment", "request-changes"])
    @pytest.mark.parametrize(
        "exit_status",
        [1, 124, 137, 143],
        ids=["gh-failed", "cap-kill-124", "cap-kill-sigkill-137", "cap-kill-sigterm-143"],
    )
    def test_gh_pr_review_failure_exits_nonzero_and_consumes_the_marker(
        self, isolated_home, git_repo, tmp_path, verdict, exit_status
    ):
        _seed_session(isolated_home, SID)
        _, body_hash = _write_findings_body(isolated_home)
        marker_head = head_sha(git_repo)
        _write_marker(isolated_home, git_repo, marker_head, body_hash)
        marker = review_pr_completion_marker_path(isolated_home, git_repo, SID)

        result, call_log = _run(
            git_repo,
            isolated_home,
            [verdict, PR_IDENTITY],
            tmp_path,
            pr_view_head_ref_oid=marker_head,
            pr_review_exit_status=exit_status,
        )
        assert result.returncode != 0
        assert len(_read_pr_review_calls(call_log)) == 1, (
            "the failing gh pr review call must still have been attempted"
        )
        assert not marker.exists(), (
            "a failed post's outcome is unknown, so it must consume the completion marker"
        )
        assert "unknown" in result.stderr
        assert PR_IDENTITY in result.stderr
        assert "marker.sh write review-pr" not in result.stderr
        assert "Ask the human to check" in result.stderr

    def test_retry_after_a_failed_post_fails_closed_without_a_second_gh_pr_review(
        self, isolated_home, git_repo, tmp_path
    ):
        _seed_session(isolated_home, SID)
        _, body_hash = _write_findings_body(isolated_home)
        marker_head = head_sha(git_repo)
        _write_marker(isolated_home, git_repo, marker_head, body_hash)

        first, call_log = _run(
            git_repo, isolated_home, ["comment", PR_IDENTITY], tmp_path,
            pr_view_head_ref_oid=marker_head, pr_review_exit_status=124,
        )
        assert first.returncode != 0
        second, call_log = _run(
            git_repo, isolated_home, ["comment", PR_IDENTITY], tmp_path,
            pr_view_head_ref_oid=marker_head,
        )
        assert second.returncode != 0
        assert "completion marker" in second.stderr
        assert "previous post attempt" in second.stderr
        assert "must not post that body again" in second.stderr
        assert "if no post has been attempted, run the skill through Step 7" in second.stderr
        assert "must not post that body again" in first.stderr
        assert len(_read_pr_review_calls(call_log)) == 1, (
            "the retry must not reach gh pr review a second time"
        )


class TestTargetArgument:
    """The target names the PR the caller means to post to, and must equal
    both the marker's recorded identity and this repo's origin. Every
    refusal happens before any gh call, so the marker stays armed."""

    def test_target_naming_a_different_pr_than_the_marker_refuses(
        self, isolated_home, git_repo, tmp_path
    ):
        _seed_session(isolated_home, SID)
        _, body_hash = _write_findings_body(isolated_home)
        marker_head = head_sha(git_repo)
        _write_marker(isolated_home, git_repo, marker_head, body_hash)
        marker = review_pr_completion_marker_path(isolated_home, git_repo, SID)
        result, call_log = _run(
            git_repo, isolated_home, ["comment", f"{OWNER_REPO}#43"], tmp_path,
            pr_view_head_ref_oid=marker_head,
        )
        assert result.returncode != 0
        assert "does not match the completion marker" in result.stderr
        assert _read_calls(call_log) == []
        assert marker.exists()

    def test_target_naming_a_different_repo_than_the_marker_refuses(
        self, isolated_home, git_repo, tmp_path
    ):
        _seed_session(isolated_home, SID)
        _, body_hash = _write_findings_body(isolated_home)
        marker_head = head_sha(git_repo)
        _write_marker(isolated_home, git_repo, marker_head, body_hash)
        result, call_log = _run(
            git_repo, isolated_home, ["comment", "other-owner/bar#42"], tmp_path,
            pr_view_head_ref_oid=marker_head,
        )
        assert result.returncode != 0
        assert "does not match the completion marker" in result.stderr
        assert _read_calls(call_log) == []

    def test_target_and_marker_agreeing_on_a_repo_other_than_origin_refuses(
        self, isolated_home, git_repo, tmp_path
    ):
        """The marker and the target agree with each other but not with the
        repository being worked in -- the cross-repo post the origin check
        exists to stop."""
        _seed_session(isolated_home, SID)
        _, body_hash = _write_findings_body(isolated_home)
        marker_head = head_sha(git_repo)
        other_identity = "other-owner/other-repo#42"
        _write_marker(isolated_home, git_repo, marker_head, body_hash, pr_identity=other_identity)
        marker = review_pr_completion_marker_path(isolated_home, git_repo, SID)
        result, call_log = _run(
            git_repo, isolated_home, ["comment", other_identity], tmp_path,
            pr_view_head_ref_oid=marker_head,
        )
        assert result.returncode != 0
        assert "origin remote" in result.stderr
        assert _read_calls(call_log) == []
        assert marker.exists()

    def test_repo_without_an_origin_remote_refuses(self, isolated_home, git_repo, tmp_path):
        _seed_session(isolated_home, SID)
        _, body_hash = _write_findings_body(isolated_home)
        marker_head = head_sha(git_repo)
        _write_marker(isolated_home, git_repo, marker_head, body_hash)
        subprocess.run(["git", "remote", "remove", "origin"], cwd=git_repo, check=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS)
        result, call_log = _run(
            git_repo, isolated_home, ["comment", PR_IDENTITY], tmp_path,
            pr_view_head_ref_oid=marker_head,
        )
        assert result.returncode != 0
        assert "origin remote" in result.stderr
        assert _read_calls(call_log) == []

    def test_target_differing_from_the_marker_and_origin_only_by_case_still_posts(
        self, isolated_home, git_repo, tmp_path
    ):
        """GitHub treats owner/repo slugs case-insensitively, so a target
        spelled in a different case than the marker or origin must not
        refuse."""
        _seed_session(isolated_home, SID)
        _, body_hash = _write_findings_body(isolated_home)
        marker_head = head_sha(git_repo)
        _write_marker(isolated_home, git_repo, marker_head, body_hash)
        result, call_log = _run(
            git_repo, isolated_home, ["comment", "FOO/BAR#42"], tmp_path,
            pr_view_head_ref_oid=marker_head,
        )
        assert result.returncode == 0, result.stderr
        assert len(_read_pr_review_calls(call_log)) == 1
