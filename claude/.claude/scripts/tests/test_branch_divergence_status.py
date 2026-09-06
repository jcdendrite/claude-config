"""Tests for branch-divergence-status.sh.

No `gh` shim is needed -- the script's only external dependency is git, run
against a local bare-remote fixture built by conftest.py's helpers.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .conftest import (
    _commit,
    _curated_path_without_direnv,
    _init_repo,
    _make_feature_branch,
    _make_repo_with_remote,
)

# Path to the script under test (resolved relative to this file)
_SCRIPT = Path(__file__).parent.parent / "branch-divergence-status.sh"


def _run_script(repo: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(_SCRIPT)],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _advance_remote_default_branch(bare: Path, tmp_path: Path, message: str) -> None:
    """Push a new commit to bare's default branch from a second clone, simulating
    another contributor's push landing on origin after local last fetched."""
    bare_clone = tmp_path / "advance-clone"
    subprocess.run(["git", "clone", "-q", str(bare), str(bare_clone)], check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=bare_clone, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=bare_clone, check=True)
    _commit(bare_clone, message)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=bare_clone, check=True)


class TestInSync:
    """Local HEAD already matches origin/main -- behind-count 0."""

    def test_reports_default_branch_and_zero_behind_count(self, tmp_path):
        local, _bare = _make_repo_with_remote(tmp_path)

        result = _run_script(local)

        assert result.returncode == 0
        assert "Default branch: main" in result.stdout
        assert "Behind: 0" in result.stdout


class TestBehindWithCleanMerge:
    """Remote default branch advanced past local, no conflicting changes."""

    def test_reports_nonzero_behind_and_clean_trial_merge(self, tmp_path):
        local, bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/unrelated")
        subprocess.run(["git", "branch", "-D", "feat/unrelated"], cwd=bare, check=True)

        _advance_remote_default_branch(bare, tmp_path, "advance remote")

        result = _run_script(local)

        assert result.returncode == 0
        assert "Behind: 1" in result.stdout
        assert "CLEAN" in result.stdout


class TestBehindWithConflicts:
    """Remote default branch advanced with a change conflicting with an unpushed
    local commit on the same file."""

    def test_reports_nonzero_behind_and_names_conflicting_file(self, tmp_path):
        local, bare = _make_repo_with_remote(tmp_path)

        # Unpushed local commit editing file.txt.
        _commit(local, "local change")

        # Remote commit editing the same file.txt differently, from the same base.
        _advance_remote_default_branch(bare, tmp_path, "remote change")

        result = _run_script(local)

        assert result.returncode == 0
        assert "Behind: 1" in result.stdout
        assert "CONFLICT" in result.stdout
        assert "file.txt" in result.stdout


class TestNotAGitRepository:
    def test_exits_nonzero_with_message(self, tmp_path):
        not_a_repo = tmp_path / "not-a-repo"
        not_a_repo.mkdir()

        result = _run_script(not_a_repo)

        assert result.returncode == 1
        assert "not inside a git repository" in result.stderr


class TestNoOriginRemote:
    def test_exits_nonzero_with_message(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit(repo, "init")

        result = _run_script(repo)

        assert result.returncode == 1
        assert "could not resolve origin/HEAD" in result.stderr


class TestOriginHeadUnset:
    def test_exits_nonzero_with_message(self, tmp_path):
        local, _bare = _make_repo_with_remote(tmp_path)
        subprocess.run(["git", "remote", "set-head", "origin", "--delete"], cwd=local, check=True)

        result = _run_script(local)

        assert result.returncode == 1
        assert "could not resolve origin/HEAD" in result.stderr


class TestDanglingOriginHeadWithRemotelyLiveTarget:
    """origin/HEAD's symref resolves, but its local tracking ref is gone
    while the branch is still live on the remote. The shared helper
    verifies the local ref resolves before this script's own recovering
    fetch ever runs, so this state is never recovered."""

    def test_exits_nonzero_before_the_recovering_fetch(self, tmp_path):
        local, _bare = _make_repo_with_remote(tmp_path)
        subprocess.run(
            ["git", "update-ref", "-d", "refs/remotes/origin/main"], cwd=local, check=True
        )

        result = _run_script(local)

        assert result.returncode == 1
        assert "could not resolve origin/HEAD" in result.stderr


class TestFetchFailure:
    def test_exits_nonzero_with_message(self, tmp_path):
        local, _bare = _make_repo_with_remote(tmp_path)
        subprocess.run(
            ["git", "remote", "set-url", "origin", str(tmp_path / "nonexistent.git")],
            cwd=local, check=True,
        )

        result = _run_script(local)

        assert result.returncode == 1
        assert "fetch of origin/main failed" in result.stderr


class TestNoTimeoutBinary:
    """_curated_path_without_direnv's tool list omits timeout/gtimeout (and
    gh/direnv), so it doubles as a minimal no-timeout PATH here."""

    def test_exits_nonzero_with_message(self, tmp_path):
        local, _bare = _make_repo_with_remote(tmp_path)
        env = {"PATH": _curated_path_without_direnv(tmp_path), "HOME": os.environ.get("HOME", "")}

        result = _run_script(local, env=env)

        assert result.returncode == 1
        assert "neither 'timeout' nor 'gtimeout' is available" in result.stderr
