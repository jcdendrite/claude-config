"""Shared git-repo scaffolding helpers for the worktree-cleanup script tests
(test_cleanup_merged_branches.py, test_cleanup_idle_open_pr_worktrees.py).

These are plain helper functions, not pytest fixtures — they take `tmp_path`
(or a repo built from it) as an explicit argument rather than being injected,
matching the calling convention already established in
test_cleanup_merged_branches.py. They have no shape-specific dependency on
either script's `gh` query: building a local git repo, a feature branch, and
a worktree is identical regardless of which script is under test.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def _init_repo(path: Path) -> None:
    """Initialise a git repo with one commit and a remote pointing at itself."""
    path.mkdir(parents=True, exist_ok=True)
    # --initial-branch=main avoids depending on the system's init.defaultBranch setting,
    # which varies across git versions and CI environments.
    subprocess.run(["git", "init", "-q", "--initial-branch=main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)


def _commit(repo: Path, message: str = "commit") -> None:
    (repo / "file.txt").write_text(message + "\n")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)


def _make_repo_with_remote(tmp_path: Path) -> tuple[Path, Path]:
    """Return (local_repo, bare_remote) with origin configured and default branch set."""
    bare = tmp_path / "remote.git"
    bare.mkdir()
    subprocess.run(["git", "init", "--bare", "-q", "--initial-branch=main"], cwd=bare, check=True)

    local = tmp_path / "local"
    _init_repo(local)
    _commit(local, "init")
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=local, check=True)
    subprocess.run(["git", "push", "-q", "-u", "origin", "main"], cwd=local, check=True)
    # Set origin/HEAD so a caller relying on it can resolve the default branch
    subprocess.run(["git", "remote", "set-head", "origin", "main"], cwd=local, check=True)
    return local, bare


def _make_feature_branch(repo: Path, branch_name: str, return_to: str = "main") -> None:
    """Create and push a feature branch in repo, then return to return_to."""
    subprocess.run(["git", "checkout", "-q", "-b", branch_name], cwd=repo, check=True)
    _commit(repo, f"work on {branch_name}")
    subprocess.run(["git", "push", "-q", "origin", branch_name], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-q", return_to], cwd=repo, check=True)


def _make_worktree(repo: Path, branch_name: str, wt_path: Path) -> None:
    """Add a linked worktree for branch_name at wt_path."""
    subprocess.run(
        ["git", "worktree", "add", str(wt_path), branch_name],
        cwd=repo,
        check=True,
    )


def _dead_pid() -> int:
    """Return a pid that is guaranteed not to be running."""
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid
