"""Tests for review-pr-post.sh.

The `gh` CLI is replaced by a PATH shim that records every invocation it
receives (one JSON object per line) so tests can assert on call history --
not just the script's exit code -- for the fail-closed paths' most
important property: no `gh pr review` call.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import subprocess
import textwrap
from pathlib import Path

import pytest
from helpers import (
    REPO_ROOT,
    SCRIPTS_DIR,
    SKILLS_DIR,
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
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=repo, check=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    )
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


# `pr_author_field` value that leaves the `author` key out of the `gh pr view` JSON, as a gh that did not return the field would.
_NO_AUTHOR_KEY = object()
# `pr_author_field` default: an `author` object holding `pr_author_login`.
_AUTHOR_FROM_LOGIN = object()


def _gh_shim_source(
    call_log: Path,
    pr_view_head_ref_oid: str | None = None,
    pr_review_exit_status: int = 0,
    pr_view_exit_status: int = 0,
    marker_path: Path | None = None,
    swap_body_path: Path | None = None,
    swap_body_content: str | None = None,
    swap_body_symlink_target: Path | None = None,
    remove_marker_path: Path | None = None,
    pr_author_login: str = "someone",
    pr_author_field: object = _AUTHOR_FROM_LOGIN,
    operator_login: str = "operator",
    api_user_exit_status: int = 0,
    api_user_body: str | None = None,
    api_user_stderr: str = "",
    api_user_stdout_on_failure: bool = False,
) -> str:
    """gh shim recording every invocation.

    `pr_view_head_ref_oid`, when given, is printed in the `gh pr view
    ... --json` call's JSON object -- the script's PR-identity cross-check
    reads it to compare against the completion marker's recorded HEAD. The
    object carries only the fields the call names in `--json`, as real gh
    does: `headRefOid`, and `author` as an object holding `pr_author_login`.
    `pr_author_field` replaces the `author` value verbatim (or, as
    `_NO_AUTHOR_KEY`, leaves the key out).
    Left unset, the shim prints nothing for that call, matching a `gh`
    failure or a PR whose current headRefOid the script cannot determine.

    A `gh api user` call prints `{"login": operator_login}`, or `api_user_body`
    verbatim when that is given. A non-zero `api_user_exit_status` makes it
    print `api_user_stderr` on stderr and exit with that status instead, 124
    modeling timeout(1) killing a `gh` that outlived its cap.
    `api_user_stdout_on_failure` makes a non-zero exit print the stdout
    first, as gh does with an HTTP error body.

    Each logged call records the `GH_TOKEN` and `GH_HOST` it ran with (null
    when unset).

    `pr_review_exit_status`, when nonzero, is the exit status of a `gh pr
    review` invocation specifically -- the shim's own always-0 default
    otherwise never exercises the script's post-failure branch. 124 and 143
    model timeout(1) killing a `gh` that outlived its cap.

    `pr_view_exit_status`, when nonzero, is the exit status of a `gh pr view`
    invocation, after it prints its output (if any). Every other invocation
    exits 0 unless a status option above says otherwise.

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
    pr_view_fields = None
    if pr_view_head_ref_oid is not None:
        pr_view_fields = {"headRefOid": pr_view_head_ref_oid}
        if pr_author_field is _AUTHOR_FROM_LOGIN:
            pr_view_fields["author"] = {"login": pr_author_login}
        elif pr_author_field is not _NO_AUTHOR_KEY:
            pr_view_fields["author"] = pr_author_field
    api_user_stdout = json.dumps({"login": operator_login}) if api_user_body is None else api_user_body
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
        PR_VIEW_FIELDS = {pr_view_fields!r}
        PR_REVIEW_EXIT_STATUS = {pr_review_exit_status!r}
        PR_VIEW_EXIT_STATUS = {pr_view_exit_status!r}
        API_USER_STDOUT = {api_user_stdout!r}
        API_USER_EXIT_STATUS = {api_user_exit_status!r}
        API_USER_STDERR = {api_user_stderr!r}
        API_USER_STDOUT_ON_FAILURE = {api_user_stdout_on_failure!r}
        MARKER_PATH = {marker_path_text!r}
        REMOVE_MARKER_PATH = {remove_marker_path_text!r}
        SWAP_BODY_PATH = {swap_body_path_text!r}
        SWAP_BODY_CONTENT = {swap_body_content!r}
        SWAP_BODY_SYMLINK_TARGET = {swap_body_symlink_target_text!r}
        args = sys.argv[1:]
        record = {{
            "args": args,
            "gh_token": os.environ.get("GH_TOKEN"),
            "gh_host": os.environ.get("GH_HOST"),
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
        if args[:2] == ["api", "user"]:
            if API_USER_EXIT_STATUS:
                if API_USER_STDOUT_ON_FAILURE:
                    print(API_USER_STDOUT)
                print(API_USER_STDERR, file=sys.stderr)
                sys.exit(API_USER_EXIT_STATUS)
            print(API_USER_STDOUT)
            sys.exit(0)
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
            if PR_VIEW_FIELDS is not None:
                requested_fields = args[args.index("--json") + 1].split(",") if "--json" in args else []
                print(json.dumps({{name: value for name, value in PR_VIEW_FIELDS.items() if name in requested_fields}}))
            sys.exit(PR_VIEW_EXIT_STATUS)
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
    PR-identity cross-check's own `gh pr view` call and the `gh api user`
    lookup that only request-changes makes."""
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


class TestWiringTripwires:
    """Source-level checks that a call still routes through its cap; they do not pin the cap's value."""

    def test_the_api_user_lookup_carries_the_view_timeout_cap(self):
        # GH-1236: an uncapped `gh api user` lets a stalled lookup hang the post path before the marker is consumed.
        code = _strip_comment_lines(SCRIPT.read_text())
        assert code.count('_lib_gh "$GH_PR_VIEW_TIMEOUT_SECONDS" api user') == 1


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

    @pytest.mark.parametrize(
        ("verb", "authorship_shim_options"),
        [
            pytest.param("comment", {}, id="comment"),
            # The PR is the operator's own, so the head check winning shows it runs before the own-PR refusal.
            pytest.param(
                "request-changes",
                {"pr_author_login": "operator", "operator_login": "operator"},
                id="request-changes-on-own-pr",
            ),
        ],
    )
    def test_pr_view_head_mismatch_fails_closed(
        self, isolated_home, git_repo, tmp_path, verb, authorship_shim_options
    ):
        """The PR's own current headRefOid disagrees with the completion
        marker's recorded HEAD -- PR_NUMBER/OWNER_REPO does not name the
        reviewed PR. Must abort before ever calling `gh pr review`."""
        _seed_session(isolated_home, SID)
        _, body_hash = _write_findings_body(isolated_home)
        _write_marker(isolated_home, git_repo, head_sha(git_repo), body_hash)
        result, call_log = _run(
            git_repo,
            isolated_home,
            [verb, PR_IDENTITY],
            tmp_path,
            pr_view_head_ref_oid="f" * 40,
            **authorship_shim_options,
        )
        assert result.returncode == 2
        assert "headRefOid" in result.stderr
        assert _OWN_PR_REFUSAL_PHRASE not in result.stderr
        assert _COULD_NOT_TELL_PHRASE not in result.stderr
        assert _read_pr_review_calls(call_log) == []
        assert review_pr_completion_marker_path(isolated_home, git_repo, SID).exists(), (
            "a refusal before the claim leaves the marker armed"
        )

    @pytest.mark.parametrize("verb", ["comment", "request-changes"])
    @pytest.mark.parametrize("pr_view_exit_status", [0, 1], ids=["no-output-exit-0", "exit-1"])
    def test_pr_view_failure_fails_closed(self, isolated_home, git_repo, tmp_path, verb, pr_view_exit_status):
        """`gh pr view` itself fails or returns no output -- treated the
        same as a mismatch, not as a pass-through. For request-changes the
        author is unreadable too, and the head refusal still comes first."""
        _seed_session(isolated_home, SID)
        _, body_hash = _write_findings_body(isolated_home)
        _write_marker(isolated_home, git_repo, head_sha(git_repo), body_hash)
        result, call_log = _run(
            git_repo, isolated_home, [verb, PR_IDENTITY], tmp_path, pr_view_exit_status=pr_view_exit_status
        )
        assert result.returncode == 2
        assert "headRefOid" in result.stderr
        assert _OWN_PR_REFUSAL_PHRASE not in result.stderr
        assert _COULD_NOT_TELL_PHRASE not in result.stderr
        assert _read_pr_review_calls(call_log) == []
        assert review_pr_completion_marker_path(isolated_home, git_repo, SID).exists()


# A secret-shaped payload gh might echo on a failed call.
_GH_ERROR_PAYLOAD = "Resource not accessible leaked-token=ghp_" + "AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHHIIII"
# Phrases of the two authorship refusals, which tell a definitive refusal from a post whose outcome is unknown.
_OWN_PR_REFUSAL_PHRASE = "was authored by the account gh posts as"
_FRESH_CONFIRMATION_PHRASE = (
    "The same body can be posted with the comment verb only after the human confirms that exact body for comment"
)
_RERUN_IS_SAFE_PHRASE = "re-running the same verb after the human re-confirms is safe"
_COULD_NOT_TELL_PHRASE = "could not tell if the account gh posts as authored"
# SKILL.md tells a session to recognise either refusal by this phrase.
_NOT_CONSUMED_PHRASE = "Nothing was posted and the completion marker was not consumed"
# Logins no refusal text may contain, chosen so no word of the fixed message text can contain one.
_DISTINCT_OPERATOR_LOGIN = "op-q7x2k"
_DISTINCT_AUTHOR_LOGIN = "au-m4z9p"


def _assert_refusal_text_is_definitive(stderr: str) -> None:
    """SKILL.md keys its never-re-post rule on "unknown" and "whether the review posted".
    The post path never names the forbidden flag, no gh error text reaches stderr, and neither account's login does."""
    for forbidden_text in ("approve", "unknown", "whether the review posted", _DISTINCT_OPERATOR_LOGIN, _DISTINCT_AUTHOR_LOGIN):
        assert forbidden_text not in stderr.lower()
    assert _GH_ERROR_PAYLOAD not in stderr


class TestRequestChangesAuthorshipCheck:
    """GH-1236: a request-changes post on the operator's own PR, or one whose
    authorship cannot be established, refuses before the marker is consumed,
    so the same body can still post as a comment."""

    def _arm_marker(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        body_file, body_hash = _write_findings_body(isolated_home)
        marker_head = head_sha(git_repo)
        _write_marker(isolated_home, git_repo, marker_head, body_hash)
        return body_file, marker_head, review_pr_completion_marker_path(isolated_home, git_repo, SID)

    def test_request_changes_on_the_operators_own_pr_refuses_and_leaves_the_marker_armed(
        self, isolated_home, git_repo, tmp_path
    ):
        # GH-1236: an own-PR request-changes refuses before the marker is consumed, so the same body can still post as comment.
        _, marker_head, marker = self._arm_marker(isolated_home, git_repo)
        result, call_log = _run(
            git_repo, isolated_home, ["request-changes", PR_IDENTITY], tmp_path,
            pr_view_head_ref_oid=marker_head, pr_author_login=_DISTINCT_OPERATOR_LOGIN.upper(),
            operator_login=_DISTINCT_OPERATOR_LOGIN,
        )
        assert result.returncode == 2
        assert _OWN_PR_REFUSAL_PHRASE in result.stderr
        assert _FRESH_CONFIRMATION_PHRASE in result.stderr
        assert _NOT_CONSUMED_PHRASE in result.stderr
        assert "Abort without posting" not in result.stderr
        assert _COULD_NOT_TELL_PHRASE not in result.stderr
        assert _RERUN_IS_SAFE_PHRASE not in result.stderr
        _assert_refusal_text_is_definitive(result.stderr)
        assert _pr_review_records(call_log) == []
        assert marker.exists()

    @pytest.mark.parametrize(
        "lookup_options",
        [
            {"api_user_exit_status": 1},
            {"api_user_exit_status": 124},
            {"api_user_body": "{}"},
            {"api_user_body": "[]"},
            {"api_user_body": '"operator"'},
            {"api_user_body": "not json"},
            {"api_user_body": '{"login": "bad login"}'},
        ],
        ids=["lookup-failed", "lookup-cap-kill-124", "empty-object", "array", "bare-string", "not-json", "login-with-space"],
    )
    def test_request_changes_refuses_when_authorship_cannot_be_established(
        self, isolated_home, git_repo, tmp_path, lookup_options
    ):
        # GH-1236: an empty author reads as someone else's PR, so an unreadable account must refuse, not post.
        _, marker_head, marker = self._arm_marker(isolated_home, git_repo)
        result, call_log = _run(
            git_repo, isolated_home, ["request-changes", PR_IDENTITY], tmp_path,
            pr_view_head_ref_oid=marker_head, pr_author_login=_DISTINCT_AUTHOR_LOGIN,
            operator_login=_DISTINCT_OPERATOR_LOGIN, api_user_stderr=_GH_ERROR_PAYLOAD, **lookup_options,
        )
        assert result.returncode == 2
        self._assert_could_not_tell_refusal(result, call_log, marker)

    @staticmethod
    def _assert_could_not_tell_refusal(result, call_log, marker) -> None:
        """The refusal names the comment recovery, never posts, and leaves the marker armed."""
        assert _COULD_NOT_TELL_PHRASE in result.stderr
        assert _NOT_CONSUMED_PHRASE in result.stderr
        assert _FRESH_CONFIRMATION_PHRASE in result.stderr
        assert _RERUN_IS_SAFE_PHRASE in result.stderr
        assert "Abort without posting" not in result.stderr
        assert _OWN_PR_REFUSAL_PHRASE not in result.stderr
        _assert_refusal_text_is_definitive(result.stderr)
        assert _pr_review_records(call_log) == []
        assert marker.exists()

    def test_request_changes_refuses_when_the_lookup_exits_non_zero_even_with_a_usable_login_on_stdout(
        self, isolated_home, git_repo, tmp_path
    ):
        # GH-1236: gh prints an HTTP error body on stdout with a non-zero exit, and that body must not be read as a verdict.
        _, marker_head, marker = self._arm_marker(isolated_home, git_repo)
        result, call_log = _run(
            git_repo, isolated_home, ["request-changes", PR_IDENTITY], tmp_path,
            pr_view_head_ref_oid=marker_head, pr_author_login=_DISTINCT_AUTHOR_LOGIN,
            operator_login=_DISTINCT_OPERATOR_LOGIN, api_user_exit_status=1,
            api_user_stdout_on_failure=True,
        )
        assert result.returncode == 2
        self._assert_could_not_tell_refusal(result, call_log, marker)

    def test_request_changes_refuses_when_the_pr_author_cannot_be_read(self, isolated_home, git_repo, tmp_path):
        # GH-1236: an unreadable author must not read as someone else's PR, or an own-PR request-changes would post.
        # review_pr_author_login's own tests own the shape matrix; one shape proves the script calls it.
        _, marker_head, marker = self._arm_marker(isolated_home, git_repo)
        result, call_log = _run(
            git_repo, isolated_home, ["request-changes", PR_IDENTITY], tmp_path,
            pr_view_head_ref_oid=marker_head, operator_login=_DISTINCT_OPERATOR_LOGIN, pr_author_field=_NO_AUTHOR_KEY,
        )
        assert result.returncode == 2
        self._assert_could_not_tell_refusal(result, call_log, marker)

    def test_comment_posts_when_the_pr_author_cannot_be_read(self, isolated_home, git_repo, tmp_path):
        # GH-1236: the author is read for request-changes only; comment must not gain a failure mode.
        _, marker_head, _ = self._arm_marker(isolated_home, git_repo)
        result, call_log = _run(
            git_repo, isolated_home, ["comment", PR_IDENTITY], tmp_path,
            pr_view_head_ref_oid=marker_head, pr_author_field=_NO_AUTHOR_KEY,
        )
        assert result.returncode == 0, result.stderr
        assert len(_pr_review_records(call_log)) == 1

    @pytest.mark.parametrize(
        "refusal_options,recovery_verb,recovery_options",
        [
            ({"pr_author_login": "operator", "operator_login": "operator"}, "comment",
             {"pr_author_login": "operator", "operator_login": "operator"}),
            ({"api_user_exit_status": 1}, "comment", {"api_user_exit_status": 1}),
            ({"api_user_exit_status": 1}, "request-changes", {"pr_author_login": "someone", "operator_login": "operator"}),
        ],
        ids=["own-pr-then-comment", "lookup-failure-then-comment", "lookup-failure-then-request-changes-on-someone-elses-pr"],
    )
    def test_the_same_body_posts_once_after_a_request_changes_refusal(
        self, isolated_home, git_repo, tmp_path, refusal_options, recovery_verb, recovery_options
    ):
        # GH-1236: the recovery the refusal message offers must work in the same session.
        body_file, marker_head, marker = self._arm_marker(isolated_home, git_repo)
        refused, refused_call_log = _run(
            git_repo, isolated_home, ["request-changes", PR_IDENTITY], tmp_path,
            pr_view_head_ref_oid=marker_head, **refusal_options,
        )
        assert refused.returncode == 2
        assert _pr_review_records(refused_call_log) == []
        assert marker.exists()
        assert _NOT_CONSUMED_PHRASE in refused.stderr

        posted, call_log = _run(
            git_repo, isolated_home, [recovery_verb, PR_IDENTITY], tmp_path,
            pr_view_head_ref_oid=marker_head, **recovery_options,
        )
        assert posted.returncode == 0, posted.stderr
        (record,) = _pr_review_records(call_log)
        assert f"--{recovery_verb}" in record["args"]
        assert bytes.fromhex(record["posted_bytes_hex"]) == body_file.read_bytes()
        assert not marker.exists()

    def test_comment_on_the_operators_own_pr_makes_no_user_lookup_and_ignores_a_failing_one(
        self, isolated_home, git_repo, tmp_path
    ):
        # GH-1236: only request-changes needs the account lookup; comment must not gain a failure mode.
        _, marker_head, _ = self._arm_marker(isolated_home, git_repo)
        result, call_log = _run(
            git_repo, isolated_home, ["comment", PR_IDENTITY], tmp_path,
            pr_view_head_ref_oid=marker_head, pr_author_login="operator", operator_login="operator",
            api_user_exit_status=1,
        )
        assert result.returncode == 0, result.stderr
        calls = _read_calls(call_log)
        assert [call[:2] for call in calls] == [["pr", "view"], ["pr", "review"]]
        (record,) = _pr_review_records(call_log)
        assert "--comment" in record["args"]

    def test_request_changes_on_someone_elses_pr_looks_up_the_account_then_the_pr_then_posts(
        self, isolated_home, git_repo, tmp_path
    ):
        _, marker_head, _ = self._arm_marker(isolated_home, git_repo)
        result, call_log = _run(
            git_repo, isolated_home, ["request-changes", PR_IDENTITY], tmp_path,
            pr_view_head_ref_oid=marker_head, pr_author_login="someone", operator_login="operator",
        )
        assert result.returncode == 0, result.stderr
        calls = _read_calls(call_log)
        # GH-1236: no gh call may sit between the head check and the unlink, so the exact call sequence is the invariant.
        assert [call[:2] for call in calls] == [["api", "user"], ["pr", "view"], ["pr", "review"]]
        assert calls[0] == ["api", "user"], "the lookup carries no method, field or input flag"
        pr_view_args = calls[1]
        assert set(pr_view_args[pr_view_args.index("--json") + 1].split(",")) == {"headRefOid", "author"}
        assert not {"--jq", "-X", "-f", "--input"} & set(pr_view_args)
        assert "--request-changes" in calls[2]

    def test_the_account_lookup_and_the_post_run_on_the_same_credentials(self, isolated_home, git_repo, tmp_path):
        # GH-1236: the identity compared must be the identity that posts, so no call may override the ambient credentials.
        ambient_token = "ambient-token-value"
        ambient_host = "ghe.example.test"
        _, marker_head, _ = self._arm_marker(isolated_home, git_repo)
        result, call_log = _run(
            git_repo, isolated_home, ["request-changes", PR_IDENTITY], tmp_path,
            extra_env={
                "GH_TOKEN": ambient_token, "GH_HOST": ambient_host, "CI_CHECKS_GH_TOKEN": "checks-override-token-value",
            },
            pr_view_head_ref_oid=marker_head,
        )
        assert result.returncode == 0, result.stderr
        records = _read_records(call_log)
        assert [record["args"][:2] for record in records] == [["api", "user"], ["pr", "view"], ["pr", "review"]]
        assert {(record["gh_token"], record["gh_host"]) for record in records} == {(ambient_token, ambient_host)}

    def test_request_changes_with_an_empty_author_login_on_a_successful_fetch_posts(
        self, isolated_home, git_repo, tmp_path
    ):
        # GH-1236: an author object with an empty login is not the operator's own PR, and the operator's lookup succeeded.
        _, marker_head, _ = self._arm_marker(isolated_home, git_repo)
        result, call_log = _run(
            git_repo, isolated_home, ["request-changes", PR_IDENTITY], tmp_path,
            pr_view_head_ref_oid=marker_head, pr_author_login="",
        )
        assert result.returncode == 0, result.stderr
        assert len(_pr_review_records(call_log)) == 1


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
    @pytest.mark.parametrize("verdict", ["comment", "request-changes"])
    def test_marker_that_cannot_be_removed_refuses_before_any_post(self, isolated_home, git_repo, tmp_path, verdict):
        _seed_session(isolated_home, SID)
        _, body_hash = _write_findings_body(isolated_home)
        marker_head = head_sha(git_repo)
        _write_marker(isolated_home, git_repo, marker_head, body_hash)
        marker = review_pr_completion_marker_path(isolated_home, git_repo, SID)
        marker.parent.chmod(0o500)
        try:
            result, call_log = _run(
                git_repo, isolated_home, [verdict, PR_IDENTITY], tmp_path, pr_view_head_ref_oid=marker_head
            )
            assert marker.exists()
        finally:
            marker.parent.chmod(0o700)
        assert result.returncode != 0
        assert "could not consume the completion marker" in result.stderr
        assert _read_pr_review_calls(call_log) == []

    @pytest.mark.parametrize("verdict", ["comment", "request-changes"])
    def test_marker_consumed_by_a_racing_invocation_refuses_before_any_post(
        self, isolated_home, git_repo, tmp_path, verdict
    ):
        _seed_session(isolated_home, SID)
        _, body_hash = _write_findings_body(isolated_home)
        marker_head = head_sha(git_repo)
        _write_marker(isolated_home, git_repo, marker_head, body_hash)
        marker = review_pr_completion_marker_path(isolated_home, git_repo, SID)

        result, call_log = _run(
            git_repo, isolated_home, [verdict, PR_IDENTITY], tmp_path,
            pr_view_head_ref_oid=marker_head, remove_marker_path=marker,
        )
        assert result.returncode != 0
        assert "could not consume the completion marker" in result.stderr
        assert _read_pr_review_calls(call_log) == [], (
            "only the invocation that removes the marker may post; one that finds it already gone must not"
        )

    @pytest.mark.parametrize("verdict", ["comment", "request-changes"])
    def test_second_invocation_fails_closed_after_the_first_succeeds(
        self, isolated_home, git_repo, tmp_path, verdict
    ):
        _seed_session(isolated_home, SID)
        _, body_hash = _write_findings_body(isolated_home)
        marker_head = head_sha(git_repo)
        _write_marker(isolated_home, git_repo, marker_head, body_hash)
        marker = review_pr_completion_marker_path(isolated_home, git_repo, SID)

        first, call_log = _run(
            git_repo, isolated_home, [verdict, PR_IDENTITY], tmp_path, pr_view_head_ref_oid=marker_head
        )
        assert first.returncode == 0, first.stderr
        assert len(_read_pr_review_calls(call_log)) == 1
        assert not marker.exists(), (
            "a successful post must delete the completion marker it consumed"
        )

        second, call_log = _run(
            git_repo, isolated_home, [verdict, PR_IDENTITY], tmp_path, pr_view_head_ref_oid=marker_head
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

    @pytest.mark.parametrize("verdict", ["comment", "request-changes"])
    def test_retry_after_a_failed_post_fails_closed_without_a_second_gh_pr_review(
        self, isolated_home, git_repo, tmp_path, verdict
    ):
        _seed_session(isolated_home, SID)
        _, body_hash = _write_findings_body(isolated_home)
        marker_head = head_sha(git_repo)
        _write_marker(isolated_home, git_repo, marker_head, body_hash)

        first, call_log = _run(
            git_repo, isolated_home, [verdict, PR_IDENTITY], tmp_path,
            pr_view_head_ref_oid=marker_head, pr_review_exit_status=124,
        )
        assert first.returncode != 0
        second, call_log = _run(
            git_repo, isolated_home, [verdict, PR_IDENTITY], tmp_path,
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


SETTINGS_PATH = REPO_ROOT / "claude" / ".claude" / "settings.json"
PROJECT_SETTINGS_PATH = REPO_ROOT / ".claude" / "settings.json"
SKILL_MD_PATH = SKILLS_DIR / "review-pr" / "SKILL.md"
# review-pr scripts whose every run must stop at the permission prompt: the
# prompt is the human's approval boundary for fetches, checkouts, and posts.
PROMPT_GATED_SCRIPT_NAMES = (
    "review-pr-post.sh",
    "review-pr-acquire.sh",
    "review-pr-checkout.sh",
    "review-pr-diff.sh",
)
# review-pr scripts SKILL.md invokes with no arguments, which settings.json
# allowlists by exact match.
ZERO_ARGUMENT_ALLOWED_SCRIPT_NAMES = (
    "review-pr-findings-path.sh",
    "review-pr-finish.sh",
    "review-pr-reviewer-findings-dir.sh",
)
# Commands that run another command, so an allow entry headed by one can reach
# any gated script.
SHELL_WRAPPER_HEADS = frozenset(
    {"bash", "sh", "zsh", "dash", "env", "xargs", "exec", "eval", "sudo", "command", "nohup", "time", "timeout"}
)
_WILDCARD_CHARACTERS = "*?["
# Settings files whose allow list the gated scripts must stay out of; the
# project file is checked only when the repo carries one.
CHECKED_SETTINGS_PATHS = [path for path in (SETTINGS_PATH, PROJECT_SETTINGS_PATH) if path.exists()]


def _allow_entry_matches_script_invocation(allow_entry: str, script_name: str) -> bool:
    """True when an allow entry would permit `script_name` in at least one
    argument form. Structural rules catch a bare `Bash`, a pattern headed by a
    wildcard or a shell wrapper, and any pattern naming the script. A glob
    match against representative invocations also catches directory-wide
    wildcards such as `~/.claude/scripts/*`, with `*` as a glob and a trailing
    `:*` as a prefix wildcard."""
    if allow_entry == "Bash":
        return True
    if not (allow_entry.startswith("Bash(") and allow_entry.endswith(")")):
        return False
    pattern = allow_entry[len("Bash("):-1].strip()
    if not pattern or pattern[0] in _WILDCARD_CHARACTERS:
        return True
    command_head = re.split(r"[\s:]", pattern)[0].rsplit("/", 1)[-1]
    if command_head in SHELL_WRAPPER_HEADS or script_name in pattern:
        return True
    if pattern.endswith(":*"):
        pattern = pattern[:-2] + "*"
    script_paths = (f"~/.claude/scripts/{script_name}", f"/srv/user/.claude/scripts/{script_name}")
    invocations = [script_name, f"{script_name} {PR_IDENTITY}"]
    for script_path in script_paths:
        invocations += [
            script_path,
            f"{script_path} {PR_IDENTITY}",
            f"{script_path} comment {PR_IDENTITY}",
            f"{script_path} request-changes {PR_IDENTITY}",
        ]
    return any(fnmatch.fnmatchcase(invocation, pattern) for invocation in invocations)


class TestAllowEntryMatcher:
    """The matcher behind the settings assertions below must catch every
    wildcard, wrapper, and absolute-path shape and leave exact zero-argument
    entries alone, or those assertions pass vacuously."""

    @pytest.mark.parametrize(
        "allow_entry",
        [
            "Bash(~/.claude/scripts/review-pr-post.sh)",
            "Bash(~/.claude/scripts/review-pr-post.sh comment foo/bar#42)",
            "Bash(~/.claude/scripts/review-pr-post.sh:*)",
            "Bash(~/.claude/scripts/review-pr-*)",
            "Bash(~/.claude/scripts/*)",
            "Bash(*)",
            "Bash(review-pr-post.sh *)",
            "Bash",
            "Bash(bash *)",
            "Bash(/srv/*/.claude/scripts/review-pr-post.sh *)",
            "Bash(/srv/*/.claude/scripts/*)",
        ],
    )
    def test_matches_an_entry_that_would_allow_the_post_script(self, allow_entry):
        assert _allow_entry_matches_script_invocation(allow_entry, "review-pr-post.sh")

    @pytest.mark.parametrize(
        "allow_entry",
        [
            "Bash(~/.claude/scripts/review-pr-finish.sh)",
            "Bash(~/.claude/scripts/review-pr-findings-path.sh)",
            "Bash(~/.claude/scripts/marker.sh status)",
            "Bash(shellcheck claude/.claude/scripts/marker.sh)",
            "Read(~/.claude/scripts/*)",
        ],
    )
    def test_does_not_match_an_unrelated_or_exact_zero_argument_entry(self, allow_entry):
        assert not _allow_entry_matches_script_invocation(allow_entry, "review-pr-post.sh")


class TestSkillMdKeysOnTheRefusalPhrase:
    def test_skill_md_names_the_phrase_both_request_changes_refusals_carry(self):
        # SKILL.md Step 8 tells a session to recognise either refusal by this phrase, so the two must not drift apart.
        assert _NOT_CONSUMED_PHRASE in SKILL_MD_PATH.read_text()


class TestSettingsAllowlistKeepsReviewPrFetchAndPostBehindThePrompt:
    """Reads the shipped settings.json and the repo-root project settings.json
    when present. It does not cover settings.local.json or any other settings
    scope, and it ignores ask/deny precedence, so a green result proves only
    that no checked allow entry reaches a gated script."""

    @staticmethod
    def _allow_entries(settings_path: Path = SETTINGS_PATH) -> list[str]:
        return json.loads(settings_path.read_text())["permissions"]["allow"]

    @pytest.mark.parametrize(
        "settings_path", CHECKED_SETTINGS_PATHS, ids=lambda path: "shipped" if path == SETTINGS_PATH else "project"
    )
    @pytest.mark.parametrize("script_name", PROMPT_GATED_SCRIPT_NAMES)
    def test_no_allow_entry_matches_a_prompt_gated_script_in_any_argument_form(self, settings_path, script_name):
        matching_entries = [
            entry
            for entry in self._allow_entries(settings_path)
            if _allow_entry_matches_script_invocation(entry, script_name)
        ]
        assert matching_entries == []

    @pytest.mark.parametrize("script_name", ZERO_ARGUMENT_ALLOWED_SCRIPT_NAMES)
    def test_zero_argument_allow_entry_matches_every_literal_skill_md_invocation(self, script_name):
        script_path = f"~/.claude/scripts/{script_name}"
        skill_md_invocations = {
            line.strip()
            for line in SKILL_MD_PATH.read_text().splitlines()
            if line.strip().split(" ")[0] == script_path
        }
        allowed_commands = {
            entry[len("Bash("):-1] for entry in self._allow_entries() if entry.startswith("Bash(") and entry.endswith(")")
        }
        assert skill_md_invocations == {script_path}
        assert script_path in allowed_commands
