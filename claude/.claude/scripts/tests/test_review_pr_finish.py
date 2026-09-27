"""Tests for review-pr-finish.sh -- the single cleanup call for
/review-pr's deliver step, run on every exit path (posted, declined, or
aborted). Idempotent, and safe to run with nothing in flight.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import SCRIPTS_DIR, head_sha, review_pr_completion_marker_path

from .conftest import _git_shim_that_fails_on_worktree_prune, _seed_session

SCRIPT = SCRIPTS_DIR / "review-pr-finish.sh"
OWNER_REPO = "foo/bar"
PR_NUMBER = "42"
PR_IDENTITY = f"{OWNER_REPO}#{PR_NUMBER}"
SID = "test-session-review-pr-finish"


@pytest.fixture
def isolated_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    return home


def _worktree_dir(repo: Path, owner_repo: str = OWNER_REPO, pr_number: str = PR_NUMBER) -> Path:
    return repo / ".claude" / "worktrees" / f"review-pr-{owner_repo.replace('/', '-')}-{pr_number}"


def _build_repo_with_review_worktree(
    tmp_path: Path, owner_repo: str = OWNER_REPO, pr_number: str = PR_NUMBER,
    *, with_origin: bool = True,
) -> tuple[Path, Path, str]:
    """A repo with a linked worktree at the shape review-pr-checkout.sh
    creates -- returns (repo, worktree_dir, head_sha).

    with_origin=True (the default) adds an `origin` remote whose own path
    embeds owner_repo's two path segments, mirroring conftest.py's
    _build_repo_with_pr_ref -- so review-pr-finish.sh's own origin-identity
    check (comparing the provenance-declared owner/repo against the
    worktree's actual origin) sees the same value a test's provenance
    declares. with_origin=False models a repo with no origin remote
    configured at all, distinct from a remote that resolves to a
    *different* owner/repo (TestOwnerRepoOriginMismatch below)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "file.txt").write_text("main\n")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    sha = head_sha(repo)

    if with_origin:
        bare = tmp_path / "remote" / owner_repo
        bare.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "--bare"], cwd=bare, check=True)
        subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=repo, check=True)

    worktree_dir = _worktree_dir(repo, owner_repo, pr_number)
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(worktree_dir), sha], cwd=repo, check=True
    )
    return repo, worktree_dir, sha


def _write_provenance(
    home: Path, mode: str, head_ref_oid: str, pr_identity: str = PR_IDENTITY,
    session_id: str = SID, pid: int = 999,
) -> Path:
    active_dir = home / ".claude" / ".review-pr-active.d"
    active_dir.mkdir(parents=True, exist_ok=True)
    provenance = active_dir / f"{session_id}.provenance"
    # Field order matches the completion marker's own (PR identity,
    # headRefOid, body hash/PID, mode): mode is the fourth field, not the
    # third.
    provenance.write_text(f"{pr_identity}\n{head_ref_oid}\n{pid}\n{mode}\n")
    return provenance


def _write_artifacts(home: Path, session_id: str = SID) -> list[Path]:
    active_dir = home / ".claude" / ".review-pr-active.d"
    active_dir.mkdir(parents=True, exist_ok=True)
    paths = [
        active_dir / f"{session_id}.body",
        active_dir / f"{session_id}.diff",
        active_dir / f"{session_id}.context.json",
    ]
    for p in paths:
        p.write_text("content\n")
    return paths


def _write_completion_marker(home: Path, repo_for_hash: Path, session_id: str = SID) -> Path:
    marker = review_pr_completion_marker_path(home, repo_for_hash, session_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"{PR_IDENTITY}\nabc\ndef\ncheckout\n")
    return marker


def _run(cwd: Path, home: Path) -> subprocess.CompletedProcess:
    env = {"HOME": str(home)}
    env.pop("CLAUDE_CONFIG_DIR", None)
    return subprocess.run(
        ["bash", str(SCRIPT)], cwd=cwd, env=env, capture_output=True, text=True,
    )


def _run_with_extra_path(cwd: Path, home: Path, shim_dir: Path) -> subprocess.CompletedProcess:
    """Same invocation as _run, but with shim_dir prepended to a real,
    inherited PATH -- needed only when a test must override one specific
    external tool (git, sha256sum) the script or a helper it shells out to
    calls internally."""
    env = {**os.environ, "HOME": str(home), "PATH": f"{shim_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
    # CLAUDE_CONFIG_DIR takes priority over HOME in _lib_config_dir -- must
    # not leak in from the ambient test-runner environment, or this
    # invocation would resolve outside the isolated `home` fixture.
    env.pop("CLAUDE_CONFIG_DIR", None)
    return subprocess.run(
        ["bash", str(SCRIPT)], cwd=cwd, env=env, capture_output=True, text=True,
    )


def _sha256sum_shim_that_produces_no_output(tmp_path: Path) -> Path:
    """A `sha256sum` PATH shim producing no output on every invocation.
    _lib_hash_diff_text treats empty sha256sum output as a hashing failure,
    so this forces _marker_lib_repo_hash to fail the same way a genuine
    sha256sum misbehavior would."""
    shim_dir = tmp_path / "sha256sum_shim"
    shim_dir.mkdir()
    shim = shim_dir / "sha256sum"
    shim.write_text("#!/usr/bin/env bash\nexit 0\n")
    shim.chmod(0o755)
    return shim_dir


class TestUsageErrors:
    def test_any_argument_exits_two_with_usage(self, isolated_home, tmp_path):
        _seed_session(isolated_home, SID)
        env = {"HOME": str(isolated_home)}
        env.pop("CLAUDE_CONFIG_DIR", None)
        result = subprocess.run(
            ["bash", str(SCRIPT), "unexpected"], cwd=tmp_path, env=env, capture_output=True, text=True,
        )
        assert result.returncode == 2
        assert "Usage" in result.stderr


class TestNothingInFlight:
    def test_exits_zero_with_no_provenance_at_all(self, isolated_home, tmp_path):
        _seed_session(isolated_home, SID)
        result = _run(tmp_path, isolated_home)
        assert result.returncode == 0, result.stderr

    def test_is_idempotent_across_two_consecutive_runs(self, isolated_home, tmp_path):
        _seed_session(isolated_home, SID)
        first = _run(tmp_path, isolated_home)
        assert first.returncode == 0, first.stderr
        second = _run(tmp_path, isolated_home)
        assert second.returncode == 0, second.stderr


class TestCheckoutModeRemovesWorktreeAndArtifacts:
    def test_removes_every_artifact_and_the_worktree(self, isolated_home, tmp_path):
        _seed_session(isolated_home, SID)
        repo, worktree_dir, sha = _build_repo_with_review_worktree(tmp_path)
        _write_provenance(isolated_home, mode="checkout", head_ref_oid=sha)
        artifact_paths = _write_artifacts(isolated_home)
        marker = _write_completion_marker(isolated_home, worktree_dir)

        result = _run(worktree_dir, isolated_home)
        assert result.returncode == 0, result.stderr

        assert not worktree_dir.exists()
        # The lock file itself is never removed: flock keys on inode, not
        # path, so deleting it while another process holds or awaits a lock
        # on that inode would let a third process reacquire a fresh lock on
        # the recreated path concurrently.
        assert Path(f"{worktree_dir}.lock").exists()
        for p in artifact_paths:
            assert not p.exists()
        provenance = isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.provenance"
        assert not provenance.exists()
        assert not marker.exists()

    def test_is_idempotent_after_a_successful_checkout_mode_run(self, isolated_home, tmp_path):
        _seed_session(isolated_home, SID)
        repo, worktree_dir, sha = _build_repo_with_review_worktree(tmp_path)
        _write_provenance(isolated_home, mode="checkout", head_ref_oid=sha)
        _write_artifacts(isolated_home)
        _write_completion_marker(isolated_home, worktree_dir)

        first = _run(worktree_dir, isolated_home)
        assert first.returncode == 0, first.stderr

        # cwd for the second run can no longer be the removed worktree --
        # the repo's own main tree is where a session would actually sit
        # once the worktree it was standing in is gone.
        second = _run(repo, isolated_home)
        assert second.returncode == 0, second.stderr


class TestDiffOnlyModeTouchesNoWorktree:
    def test_removes_artifacts_and_marker_without_touching_any_worktree(
        self, isolated_home, tmp_path
    ):
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
        (repo / "file.txt").write_text("main\n")
        subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        sha = head_sha(repo)

        _seed_session(isolated_home, SID)
        _write_provenance(isolated_home, mode="diff-only", head_ref_oid=sha)
        artifact_paths = _write_artifacts(isolated_home)
        marker = _write_completion_marker(isolated_home, repo)

        worktree_dir = _worktree_dir(repo)
        assert not worktree_dir.exists(), "diff-only mode never creates a worktree in the first place"

        result = _run(repo, isolated_home)
        assert result.returncode == 0, result.stderr

        assert not worktree_dir.exists()
        for p in artifact_paths:
            assert not p.exists()
        assert not marker.exists()


class TestAcquireOnlyModeRemovesArtifactsOnly:
    def test_mode_acquired_removes_artifacts_but_touches_no_worktree(self, isolated_home, tmp_path):
        """mode "acquired" (Step 1 ran, but neither review-pr-checkout.sh
        nor review-pr-diff.sh ever did) must still clean up cleanly -- no
        worktree was ever created for this mode either."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
        (repo / "file.txt").write_text("main\n")
        subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        sha = head_sha(repo)

        _seed_session(isolated_home, SID)
        _write_provenance(isolated_home, mode="acquired", head_ref_oid=sha)
        artifact_paths = _write_artifacts(isolated_home)

        result = _run(repo, isolated_home)
        assert result.returncode == 0, result.stderr
        for p in artifact_paths:
            assert not p.exists()
        provenance = isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.provenance"
        assert not provenance.exists()


class TestOwnerRepoOriginMismatch:
    """Mirrors review-pr-checkout.sh's/review-pr-diff.sh's own
    TestOwnerRepoOriginMismatch/TestOriginMismatch classes: a forged or
    stale provenance OWNER_REPO must not be able to direct this script's
    own `git worktree remove --force`/`rm -rf` at a colliding-but-unrelated
    worktree."""

    def test_mismatched_owner_repo_never_removes_the_colliding_worktree(
        self, isolated_home, tmp_path
    ):
        _seed_session(isolated_home, SID)
        repo, _own_worktree_dir, sha = _build_repo_with_review_worktree(tmp_path)
        decoy_owner_repo = "decoy-owner/decoy-repo"
        # A forged provenance identity can compute the same path as a real,
        # unrelated in-progress worktree.
        # That collision must not cause the removal call to delete the
        # colliding worktree.
        decoy_worktree_dir = _worktree_dir(repo, decoy_owner_repo, PR_NUMBER)
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(decoy_worktree_dir), sha],
            cwd=repo, check=True,
        )
        _write_provenance(
            isolated_home, mode="checkout", head_ref_oid=sha,
            pr_identity=f"{decoy_owner_repo}#{PR_NUMBER}",
        )
        artifact_paths = _write_artifacts(isolated_home)

        result = _run(repo, isolated_home)
        assert result.returncode == 0, result.stderr
        assert "origin" in result.stderr

        assert decoy_worktree_dir.exists(), "an origin mismatch must never remove a colliding worktree"
        for p in artifact_paths:
            assert not p.exists(), "artifact cleanup must still proceed when worktree removal is skipped"

    def test_no_origin_remote_configured_never_removes_the_worktree(self, isolated_home, tmp_path):
        """Distinct runtime state from a populated-but-mismatched origin
        above: `git remote get-url origin` itself fails, so ORIGIN_URL and
        ORIGIN_OWNER_REPO are both empty rather than merely unequal to
        OWNER_REPO -- the empty-string branch of the same check must fail
        closed identically."""
        _seed_session(isolated_home, SID)
        repo, worktree_dir, sha = _build_repo_with_review_worktree(tmp_path, with_origin=False)
        _write_provenance(isolated_home, mode="checkout", head_ref_oid=sha)
        artifact_paths = _write_artifacts(isolated_home)

        result = _run(repo, isolated_home)
        assert result.returncode == 0, result.stderr
        assert "origin" in result.stderr

        assert worktree_dir.exists(), "no origin remote at all must never remove the worktree"
        for p in artifact_paths:
            assert not p.exists(), "artifact cleanup must still proceed when worktree removal is skipped"


class TestLockContention:
    """review-pr-finish.sh's own worktree removal acquires the same lock
    review-pr-worktree-replace.py uses for the identical WORKTREE_DIR
    (both now import _review_pr_worktree.acquire_lock) -- this pins
    that review-pr-finish.sh reacts sensibly to a transient contention,
    mirroring test_review_pr_checkout.py's own
    TestWorktreeReplaceIntegration rather than re-testing the lock
    primitive itself (already unit-tested directly in
    test_review_pr_worktree_replace.py)."""

    def test_transient_lock_contention_is_waited_out_not_treated_as_failure(
        self, isolated_home, tmp_path
    ):
        _seed_session(isolated_home, SID)
        repo, worktree_dir, sha = _build_repo_with_review_worktree(tmp_path)
        _write_provenance(isolated_home, mode="checkout", head_ref_oid=sha)
        lock_path = Path(f"{worktree_dir}.lock")

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
            result = _run(repo, isolated_home)
            assert result.returncode == 0, result.stderr
            assert not worktree_dir.exists()
        finally:
            holder.wait(timeout=10)


class TestInteriorFailureStillReachesExitZero:
    """The usage banner promises "Always exits 0, whether or not anything
    was in flight" -- pinned here against three independent interior
    failures, each guarded by its own `if ...; then ... else ...` rather
    than left for `set -e` to propagate: a failing `git worktree prune`
    inside review-pr-worktree-remove.py, a failing repo-hash computation
    upstream of any worktree handling, and a `_lib_repo_root` resolution
    failure upstream of that repo-hash computation itself. Without any of
    the three guards, `set -e` would abort the script before it ever
    reaches the artifact `rm -f` at the bottom."""

    @pytest.mark.parametrize(
        "shim_factory,expected_stderr_substring,marker_survives",
        [
            pytest.param(
                _git_shim_that_fails_on_worktree_prune,
                "could not remove review worktree",
                False,
                id="worktree_prune_failure",
            ),
            pytest.param(
                _sha256sum_shim_that_produces_no_output,
                "could not compute the repo hash",
                True,
                id="repo_hash_computation_failure",
            ),
        ],
    )
    def test_exits_zero_and_still_removes_artifacts(
        self, isolated_home, tmp_path, shim_factory, expected_stderr_substring, marker_survives
    ):
        _seed_session(isolated_home, SID)
        repo, worktree_dir, sha = _build_repo_with_review_worktree(tmp_path)
        _write_provenance(isolated_home, mode="checkout", head_ref_oid=sha)
        artifact_paths = _write_artifacts(isolated_home)
        marker = _write_completion_marker(isolated_home, worktree_dir)

        shim_dir = shim_factory(tmp_path)
        result = _run_with_extra_path(worktree_dir, isolated_home, shim_dir)

        assert result.returncode == 0, result.stderr
        assert expected_stderr_substring in result.stderr
        for p in artifact_paths:
            assert not p.exists()
        provenance = isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.provenance"
        assert not provenance.exists()
        # worktree_prune_failure: the hash still resolves, so marker
        # cleanup proceeds normally. repo_hash_computation_failure: the
        # hash never resolves, so marker cleanup is skipped and the marker
        # survives.
        assert marker.exists() == marker_survives

    def test_repo_root_resolution_failure_still_removes_artifacts(self, isolated_home, tmp_path):
        """_lib_repo_root fails outside any git repository -- a step
        upstream of the two hash-adjacent failures above, since
        _marker_lib_repo_hash is never even called without a resolved repo
        root. The repo-hash section's own guard must skip marker cleanup
        without aborting the artifact removal below."""
        _seed_session(isolated_home, SID)
        _write_provenance(isolated_home, mode="diff-only", head_ref_oid="a" * 40)
        artifact_paths = _write_artifacts(isolated_home)

        result = _run(tmp_path, isolated_home)

        assert result.returncode == 0, result.stderr
        assert "could not resolve the current repository root" in result.stderr
        for p in artifact_paths:
            assert not p.exists()


class TestInteriorFailureBeforeSessionIsKnownStillExitsZero:
    """Two more "Always exits 0" guards, each a step upstream of session/
    provenance resolution: CONFIG_DIR (review-pr-finish.sh lines ~34-37)
    and SESSION_ID (lines ~39-42). Without either guard, `set -e` would
    abort the script non-zero before any cleanup is even attempted -- and
    there is nothing to clean up in either case, since CONFIG_DIR/
    SESSION_ID are what locate every other artifact."""

    def test_config_dir_resolution_failure_exits_zero(self, tmp_path):
        result = subprocess.run(
            ["bash", str(SCRIPT)], cwd=tmp_path, env={"HOME": ""}, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "could not resolve the Claude Code config directory" in result.stderr

    def test_session_id_resolution_failure_exits_zero(self, isolated_home, tmp_path):
        """isolated_home is a valid, resolvable $HOME with no session file
        seeded under it (no _seed_session call) -- resolve-session-id's own
        ancestor walk finds no match."""
        result = _run(tmp_path, isolated_home)
        assert result.returncode == 0, result.stderr
        assert "could not resolve this session's id" in result.stderr
