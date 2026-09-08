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
import textwrap
from pathlib import Path

import pytest
from helpers import SCRIPTS_DIR, SKILLS_DIR, head_sha

from .conftest import _shimmed_env

SCRIPT = SCRIPTS_DIR / "review-pr-checkout.sh"
OWNER_REPO = "foo/bar"
PR_NUMBER = "42"
PR_IDENTITY = f"{OWNER_REPO}#{PR_NUMBER}"


def _install_audit_script(home: Path) -> None:
    """Symlink the real audit-execution-surface.py into the isolated
    $HOME/.claude/skills/review-pr/ -- the installed-layout path
    review-pr-checkout.sh resolves via $CONFIG_DIR/skills/review-pr/
    (stow-packages.sh: claude-skills/ stows to ~/.claude/). Exercises the
    real predicate rather than a stand-in copy that could drift from it."""
    skill_dir = home / ".claude" / "skills" / "review-pr"
    skill_dir.mkdir(parents=True, exist_ok=True)
    target = skill_dir / "audit-execution-surface.py"
    if not target.exists():
        target.symlink_to(SKILLS_DIR / "review-pr" / "audit-execution-surface.py")


def _worktree_dir(repo: Path) -> Path:
    return repo / ".claude" / "worktrees" / f"review-pr-{OWNER_REPO.replace('/', '-')}-{PR_NUMBER}"


def _local_pr_ref_names(repo: Path) -> str:
    return subprocess.run(
        ["git", "for-each-ref", "refs/review-pr"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def isolated_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    return home


def _build_repo_with_pr_ref(
    tmp_path: Path, owner_repo: str = OWNER_REPO, pr_number: str = PR_NUMBER
) -> tuple[Path, str]:
    """A local repo with an `origin` remote and a PR ref pushed directly to
    it under `refs/pull/<N>/head` -- mirrors GitHub's synthetic per-PR ref,
    which is never a normal branch on the base repo. Returns (repo, pr_sha)
    with the working copy left checked out at the pre-PR commit, so a
    successful checkout's own worktree content is what proves the PR
    commit landed, not the main checkout's.

    The bare remote's own path embeds owner_repo's two path segments (e.g.
    `.../remote/foo/bar`), so review-pr-checkout.sh's origin-identity check
    (comparing $1's owner/repo against the worktree's actual origin) sees
    the same value callers pass as $1 -- the same last-two-path-segments
    shape a real `https://github.com/<owner>/<repo>.git` origin parses to.
    """
    bare = tmp_path / "remote" / owner_repo
    bare.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "--bare"], cwd=bare, check=True)

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "file.txt").write_text("main\n")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=repo, check=True)
    subprocess.run(["git", "push", "-q", "origin", "HEAD:refs/heads/main"], cwd=repo, check=True)
    main_sha = head_sha(repo)

    (repo / "pr_file.txt").write_text("pr change\n")
    subprocess.run(["git", "add", "pr_file.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "pr commit"], cwd=repo, check=True)
    pr_sha = head_sha(repo)
    subprocess.run(["git", "push", "-q", "origin", f"HEAD:refs/pull/{pr_number}/head"], cwd=repo, check=True)
    subprocess.run(["git", "reset", "-q", "--hard", main_sha], cwd=repo, check=True)

    return repo, pr_sha


@pytest.fixture
def repo_with_pr_ref(tmp_path):
    return _build_repo_with_pr_ref(tmp_path)


def _gh_shim_source(
    call_log: Path,
    head_ref_oid: str | None = None,
    files: list[str] | None = None,
    fail_pr_view: bool = False,
    fail_files: bool = False,
    partial_files_then_fail: list[str] | None = None,
) -> str:
    """gh shim recording every invocation, matching test_review_pr_post.py's
    own shim shape. Dispatches on the invocation's own first word(s):
    `gh pr view ... --json headRefOid` returns `head_ref_oid`; `gh api
    .../files --paginate ...` prints `files`, one per line. `fail_pr_view`/
    `fail_files` exit 1 on the matching call only, modeling a `gh` failure
    (rate limit, network) on that one endpoint without the production `gh`
    binary's own always-0 stand-in masking the script's failure branch.
    `partial_files_then_fail` prints those filenames, then exits 1 --
    `--paginate` failing partway through, after already emitting one or more
    pages, distinct from `fail_files`'s zero-output failure."""
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json
        import os
        import sys

        CALL_LOG = {str(call_log)!r}
        HEAD_REF_OID = {head_ref_oid!r}
        FILES = {list(files or [])!r}
        FAIL_PR_VIEW = {fail_pr_view!r}
        FAIL_FILES = {fail_files!r}
        PARTIAL_FILES_THEN_FAIL = {list(partial_files_then_fail or [])!r}
        args = sys.argv[1:]
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
            if HEAD_REF_OID:
                print(HEAD_REF_OID)
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
    extra_env: dict | None = None,
) -> tuple[subprocess.CompletedProcess, Path]:
    call_log = tmp_path / "gh_calls.jsonl"
    env = {
        **_shimmed_env(
            tmp_path,
            _gh_shim_source(
                call_log, head_ref_oid, files, fail_pr_view, fail_files, partial_files_then_fail
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
        api_calls = [c for c in calls if c[:1] == ["api"]]
        assert api_calls, "the file list must be self-fetched"
        assert "--paginate" in api_calls[0], (
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
