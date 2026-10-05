"""Contract tests for helpers.init_git_repo and helpers.init_git_repo_with_commit."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from helpers import _run_git, init_git_repo, init_git_repo_with_commit


def test_init_git_repo_leaves_head_unborn_on_the_requested_branch(tmp_path: Path) -> None:
    repo = init_git_repo(tmp_path / "repo", branch="probe")

    head_lookup = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"], cwd=repo, capture_output=True, check=False
    )

    assert head_lookup.returncode != 0
    assert _run_git(repo, "symbolic-ref", "--short", "HEAD").strip() == "probe"


def test_init_git_repo_sets_a_local_commit_identity(tmp_path: Path) -> None:
    repo = init_git_repo(tmp_path / "repo")

    assert _run_git(repo, "config", "--local", "user.email").strip()
    assert _run_git(repo, "config", "--local", "user.name").strip()


@pytest.mark.parametrize("builder", [init_git_repo, init_git_repo_with_commit])
def test_builder_without_a_branch_keeps_the_host_default(
    builder, tmp_path: Path, monkeypatch
) -> None:
    # Env-only config keeps the probe default out of the machine's git config. Needs git >= 2.31, which added GIT_CONFIG_COUNT.
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "init.defaultBranch")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "host-default-probe")

    repo = builder(tmp_path / "repo")

    assert _run_git(repo, "symbolic-ref", "--short", "HEAD").strip() == "host-default-probe"


def test_init_git_repo_with_commit_forwards_the_requested_branch(tmp_path: Path) -> None:
    repo = init_git_repo_with_commit(tmp_path / "repo", branch="probe")

    assert _run_git(repo, "symbolic-ref", "--short", "HEAD").strip() == "probe"


def test_init_git_repo_with_commit_tracks_the_default_seed_file(tmp_path: Path) -> None:
    repo = init_git_repo_with_commit(tmp_path / "repo")

    subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=repo, capture_output=True, check=True)
    assert _run_git(repo, "ls-files").splitlines() == ["f.txt"]
    assert _run_git(repo, "show", "HEAD:f.txt") == "x\n"


def test_init_git_repo_with_commit_tracks_a_nested_seed_file_with_its_content(tmp_path: Path) -> None:
    # The leading dash fails `git add` unless the builder passes `--` before the pathspec.
    repo = init_git_repo_with_commit(tmp_path / "repo", file_name="-a/b.txt", content="y\n")

    assert _run_git(repo, "ls-files").splitlines() == ["-a/b.txt"]
    assert _run_git(repo, "show", "HEAD:-a/b.txt") == "y\n"
