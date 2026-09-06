"""Tests for check-branch-divergence.sh.

The hook is a SessionStart hook (matcher: startup) that surfaces
feature-branch divergence from origin/<default> via
hookSpecificOutput.additionalContext. Quiet-on-success: emits nothing
when behind = 0 or when any skip-silent gate fires (not a repo,
detached HEAD, on default branch, no origin, origin/HEAD unresolvable).

Detection primitive:
    git rev-list --count HEAD..origin/<default>
    git merge-tree --write-tree origin/<default> HEAD
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from helpers import HOOKS_DIR

CHECK_BRANCH_DIVERGENCE_HOOK = HOOKS_DIR / "check-branch-divergence.sh"


def _run_hook(
    payload: dict,
    cwd: Path,
    extra_path: Path | None = None,
    path_override: Path | None = None,
) -> subprocess.CompletedProcess:
    """Invoke the hook with `payload` as JSON stdin, in `cwd`. Pass
    `extra_path` to prepend a dir to $PATH (fake-binary injection); pass
    `path_override` to replace $PATH entirely (for tests that need to
    suppress lookups of real-system binaries like `timeout`)."""
    env = {**os.environ}
    if path_override is not None:
        env["PATH"] = str(path_override)
    elif extra_path is not None:
        env["PATH"] = f"{extra_path}:{env['PATH']}"
    return subprocess.run(
        [str(CHECK_BRANCH_DIVERGENCE_HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        check=False,
    )


def _additional_context(result: subprocess.CompletedProcess) -> str:
    return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]


# ---------- fixtures ----------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _git_q(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def bare_remote(tmp_path):
    """Bare repo to act as `origin` with a single commit on `main`."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    seed = tmp_path / "seed"
    seed.mkdir()
    _git_q(seed, "init", "-q", "-b", "main")
    _git_q(seed, "config", "user.email", "t@t.com")
    _git_q(seed, "config", "user.name", "t")
    (seed / "f").write_text("a\n")
    _git_q(seed, "add", "f")
    _git_q(seed, "commit", "-qm", "init")
    _git_q(seed, "remote", "add", "origin", str(bare))
    _git_q(seed, "push", "-q", "origin", "main")
    return bare


@pytest.fixture
def feature_clone(tmp_path, bare_remote):
    """Clone of `bare_remote` checked out on a feature branch with
    origin/HEAD properly set. Use this as the base for branch-state
    fixtures."""
    repo = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", str(bare_remote), str(repo)],
        check=True,
        capture_output=True,
    )
    _git_q(repo, "config", "user.email", "t@t.com")
    _git_q(repo, "config", "user.name", "t")
    _git_q(repo, "remote", "set-head", "origin", "main")
    _git_q(repo, "checkout", "-q", "-b", "feature")
    return repo


@pytest.fixture
def repo_in_sync(feature_clone):
    """Feature branch caught up with origin/main (behind = 0)."""
    return feature_clone


@pytest.fixture
def repo_behind_clean(feature_clone, bare_remote, tmp_path):
    """Feature branch is N commits behind origin/main; trial merge is
    clean (the new commit lands in a file untouched by the feature
    branch)."""
    push = tmp_path / "push-clean"
    subprocess.run(
        ["git", "clone", "-q", str(bare_remote), str(push)],
        check=True,
        capture_output=True,
    )
    _git_q(push, "config", "user.email", "t@t.com")
    _git_q(push, "config", "user.name", "t")
    (push / "newfile").write_text("clean-addition\n")
    _git_q(push, "add", "newfile")
    _git_q(push, "commit", "-qm", "add newfile")
    _git_q(push, "push", "-q", "origin", "main")
    _git_q(feature_clone, "fetch", "-q", "origin")
    return feature_clone


@pytest.fixture
def repo_behind_conflict(feature_clone, bare_remote, tmp_path):
    """Feature branch is N commits behind origin/main AND has local
    edits to a file that origin also changed → trial merge conflicts."""
    (feature_clone / "f").write_text("feature-edit\n")
    _git_q(feature_clone, "add", "f")
    _git_q(feature_clone, "commit", "-qm", "feature edits f")

    push = tmp_path / "push-conflict"
    subprocess.run(
        ["git", "clone", "-q", str(bare_remote), str(push)],
        check=True,
        capture_output=True,
    )
    _git_q(push, "config", "user.email", "t@t.com")
    _git_q(push, "config", "user.name", "t")
    (push / "f").write_text("origin-edit\n")
    _git_q(push, "add", "f")
    _git_q(push, "commit", "-qm", "origin edits f")
    _git_q(push, "push", "-q", "origin", "main")
    _git_q(feature_clone, "fetch", "-q", "origin")
    return feature_clone


def _advance_remote_default_branch(bare: Path, tmp_path: Path, message: str) -> None:
    """Push a new commit to bare's default branch from a second clone, simulating
    another contributor's push landing on origin after local last fetched."""
    push = tmp_path / "advance-clone"
    subprocess.run(["git", "clone", "-q", str(bare), str(push)], check=True, capture_output=True)
    _git_q(push, "config", "user.email", "t@t.com")
    _git_q(push, "config", "user.name", "t")
    (push / "f").write_text(f"{message}\n")
    _git_q(push, "add", "f")
    _git_q(push, "commit", "-qm", message)
    _git_q(push, "push", "-q", "origin", "main")


def _make_fake_git(bin_dir: Path, fetch_exit: int) -> Path:
    """Write a shim at $bin_dir/git that exits `fetch_exit` for any
    `git fetch ...` and otherwise proxies through to the real git
    (resolved via $REAL_GIT in the calling environment)."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "git"
    shim.write_text(
        '#!/bin/bash\n'
        'if [ "$1" = "fetch" ]; then\n'
        f'  exit {fetch_exit}\n'
        'fi\n'
        'exec "$REAL_GIT" "$@"\n'
    )
    shim.chmod(0o755)
    return shim


_TIMEOUT_FREE_PATH_BINS = ("git", "jq", "awk", "sort", "paste", "bash", "tr", "cat", "dirname")


def _make_timeout_free_path(tmp_path: Path) -> Path:
    """Build a dir of symlinks to every binary the hook calls EXCEPT
    `timeout` and `gtimeout`. Used as a *replacement* $PATH (not a
    prefix) so the lookups for those two binaries fail even on Linux
    where they exist in /usr/bin. `command -v` requires the file to be
    executable, so masking with a non-executable shim is insufficient —
    bash would still find the real binary further down $PATH."""
    bin_dir = tmp_path / "no-timeout-path"
    bin_dir.mkdir()
    for name in _TIMEOUT_FREE_PATH_BINS:
        src = subprocess.run(
            ["which", name],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        (bin_dir / name).symlink_to(src)
    return bin_dir


# ---------- tests -------------------------------------------------------


class TestCheckBranchDivergence:
    # (a) cwd not in a repo → exit 0 silent
    def test_not_in_git_repo(self, tmp_path):
        non_repo = tmp_path / "plain"
        non_repo.mkdir()
        result = _run_hook({}, cwd=non_repo)
        assert result.returncode == 0
        assert result.stdout == ""

    # (b) HEAD on default branch → exit 0 silent
    def test_on_default_branch(self, feature_clone):
        _git_q(feature_clone, "checkout", "-q", "main")
        result = _run_hook({}, cwd=feature_clone)
        assert result.returncode == 0
        assert result.stdout == ""

    # (c) no `origin` remote → exit 0 silent
    def test_no_origin_remote(self, tmp_path):
        repo = tmp_path / "no-origin"
        repo.mkdir()
        _git_q(repo, "init", "-q", "-b", "main")
        _git_q(repo, "config", "user.email", "t@t.com")
        _git_q(repo, "config", "user.name", "t")
        (repo / "f").write_text("a\n")
        _git_q(repo, "add", "f")
        _git_q(repo, "commit", "-qm", "init")
        _git_q(repo, "checkout", "-q", "-b", "feature")
        result = _run_hook({}, cwd=repo)
        assert result.returncode == 0
        assert result.stdout == ""

    # (d) behind = 0 (fresh fetch) → exit 0 silent
    def test_behind_zero_silent(self, repo_in_sync):
        result = _run_hook({}, cwd=repo_in_sync)
        assert result.returncode == 0
        assert result.stdout == ""

    # (e) behind > 0, trial merge clean → advisory with CLEAN line + JSON envelope
    def test_behind_clean_emits_clean_advisory(self, repo_behind_clean):
        result = _run_hook({}, cwd=repo_behind_clean)
        assert result.returncode == 0
        parsed = json.loads(result.stdout)
        hook_output = parsed["hookSpecificOutput"]
        assert hook_output["hookEventName"] == "SessionStart"
        assert isinstance(hook_output["additionalContext"], str)
        ctx = hook_output["additionalContext"]
        assert "commits behind" in ctx
        assert "origin/main" in ctx
        assert "Trial merge: CLEAN" in ctx
        assert "/git-feature-branch-sync" in ctx

    # (f) behind > 0, trial merge conflict → advisory lists conflict files
    def test_behind_conflict_lists_files(self, repo_behind_conflict):
        result = _run_hook({}, cwd=repo_behind_conflict)
        assert result.returncode == 0
        ctx = _additional_context(result)
        assert "Trial merge: CONFLICT in:" in ctx
        assert "f" in ctx  # the conflicting file

    # (g1) fetch fails + locally-cached origin/main shows behind = 0 → exit 0 silent
    def test_fetch_fail_cached_behind_zero(self, repo_in_sync, tmp_path):
        bin_dir = tmp_path / "fake-bin-g1"
        _make_fake_git(bin_dir, fetch_exit=124)
        env_real_git = subprocess.run(
            ["which", "git"], capture_output=True, text=True, check=True
        ).stdout.strip()
        result = subprocess.run(
            [str(CHECK_BRANCH_DIVERGENCE_HOOK)],
            input=json.dumps({}),
            capture_output=True,
            text=True,
            cwd=repo_in_sync,
            env={
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "REAL_GIT": env_real_git,
            },
            check=False,
        )
        assert result.returncode == 0
        assert result.stdout == ""

    # (g2) fetch fails + locally-cached origin/main shows behind > 0 → stale-ref advisory
    def test_fetch_fail_cached_behind_positive(self, repo_behind_clean, tmp_path):
        bin_dir = tmp_path / "fake-bin-g2"
        _make_fake_git(bin_dir, fetch_exit=124)
        env_real_git = subprocess.run(
            ["which", "git"], capture_output=True, text=True, check=True
        ).stdout.strip()
        result = subprocess.run(
            [str(CHECK_BRANCH_DIVERGENCE_HOOK)],
            input=json.dumps({}),
            capture_output=True,
            text=True,
            cwd=repo_behind_clean,
            env={
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "REAL_GIT": env_real_git,
            },
            check=False,
        )
        assert result.returncode == 0
        ctx = _additional_context(result)
        assert "commits behind" in ctx
        assert "stale ref" in ctx
        assert "Trial merge:" not in ctx  # skipped in fallback

    # (h) origin remote exists but refs/remotes/origin/HEAD is unset → exit 0 silent
    def test_origin_head_unset(self, tmp_path, bare_remote):
        repo = tmp_path / "origin-head-unset"
        subprocess.run(
            ["git", "clone", "-q", str(bare_remote), str(repo)],
            check=True,
            capture_output=True,
        )
        _git_q(repo, "config", "user.email", "t@t.com")
        _git_q(repo, "config", "user.name", "t")
        # `git clone` sets origin/HEAD; delete it to exercise the unresolvable path.
        _git_q(repo, "remote", "set-head", "--delete", "origin")
        _git_q(repo, "checkout", "-q", "-b", "feature")
        result = _run_hook({}, cwd=repo)
        assert result.returncode == 0
        assert result.stdout == ""

    # (h2) origin/HEAD dangles locally but its target is still live on the
    # remote → exit 0 silent, no self-heal. Distinct from (h): there the
    # symref itself is absent; here the symref resolves but its target ref
    # is gone, and the shared helper verifies the local ref before this
    # hook's own bounded fetch ever runs, so recovery never happens.
    #
    # The remote is advanced past the local clone first: without a real
    # behind-count to report, this test would pass identically whether or
    # not the pre-refactor hook's self-heal-via-fetch ran, since a fetch
    # against an undiverged remote produces no advisory either way.
    def test_dangling_origin_head_with_remotely_live_target_silent(
        self, feature_clone, bare_remote, tmp_path
    ):
        _advance_remote_default_branch(bare_remote, tmp_path, "advance remote")
        _git_q(feature_clone, "update-ref", "-d", "refs/remotes/origin/main")
        result = _run_hook({}, cwd=feature_clone)
        assert result.returncode == 0
        assert result.stdout == ""

    # (i) HEAD detached → exit 0 silent
    def test_detached_head(self, feature_clone):
        head_sha = _git(feature_clone, "rev-parse", "HEAD").strip()
        _git_q(feature_clone, "checkout", "-q", "--detach", head_sha)
        result = _run_hook({}, cwd=feature_clone)
        assert result.returncode == 0
        assert result.stdout == ""

    # (j) neither timeout nor gtimeout on $PATH → fetch skipped, stale-ref fallback
    def test_no_timeout_wrapper_falls_back(self, repo_behind_clean, tmp_path):
        timeout_free = _make_timeout_free_path(tmp_path)
        result = _run_hook({}, cwd=repo_behind_clean, path_override=timeout_free)
        assert result.returncode == 0
        ctx = _additional_context(result)
        assert "stale ref" in ctx
        assert "Trial merge:" not in ctx
