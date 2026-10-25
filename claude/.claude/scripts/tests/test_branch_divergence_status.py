"""Tests for branch-divergence-status.sh.

The script's only external dependency is git, run against a local bare-remote
fixture built by conftest.py's helpers. Every run puts a fail-loud `gh` shim on
PATH and asserts the script never called it.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

from helpers import _make_git_exiting_with_status

from .conftest import (
    _STACKED_BRANCH,
    _commit,
    _curated_path_without_direnv,
    _init_repo,
    _make_feature_branch,
    _make_repo_with_remote,
    _make_stacked_repo,
)

# Path to the script under test (resolved relative to this file)
_SCRIPT = Path(__file__).parent.parent / "branch-divergence-status.sh"


def _run_script(repo: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run the script with a gh shim first on PATH that records any call and
    exits 97, then assert no call was recorded: the script is gh-free."""
    shim_dir = repo.parent / "fail-loud-gh-bin"
    calls_log = repo.parent / "gh-calls.log"
    shim_dir.mkdir(exist_ok=True)
    gh_shim = shim_dir / "gh"
    gh_shim.write_text(f'#!/bin/sh\necho "$@" >> {shlex.quote(str(calls_log))}\nexit 97\n')
    gh_shim.chmod(0o755)
    run_env = dict(os.environ) if env is None else dict(env)
    run_env["PATH"] = os.pathsep.join([str(shim_dir), run_env.get("PATH", "")])
    result = subprocess.run(
        [str(_SCRIPT)],
        cwd=str(repo),
        env=run_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert not calls_log.exists(), f"script called gh: {calls_log.read_text()}"
    return result


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


class TestBehindWithUnrelatedHistories:
    """HEAD is an orphan branch, so `git merge-tree` refuses to merge it with
    origin/main and no verdict can be computed."""

    def test_exits_nonzero_without_a_trial_merge_line_naming_the_failure_and_git(self, tmp_path):
        local, _bare = _make_repo_with_remote(tmp_path)
        subprocess.run(["git", "checkout", "-q", "--orphan", "orphan-feature"], cwd=local, check=True)
        _commit(local, "orphan work")

        result = _run_script(local)

        assert result.returncode == 1
        assert "Behind: 1" in result.stdout
        assert "Trial merge:" not in result.stdout
        assert "trial merge of origin/main into HEAD could not complete" in result.stderr
        assert "git version" in result.stderr


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


def _advance_remote_stack(bare: Path, tmp_path: Path) -> None:
    """Push a commit adding remote.txt to bare's `stack` branch from a second clone."""
    clone = tmp_path / "advance-stack-clone"
    subprocess.run(["git", "clone", "-q", "-b", "stack", str(bare), str(clone)], check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=clone, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=clone, check=True)
    (clone / "remote.txt").write_text("remote stack work\n")
    subprocess.run(["git", "add", "remote.txt"], cwd=clone, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "advance stack"], cwd=clone, check=True)
    subprocess.run(["git", "push", "-q", "origin", "stack"], cwd=clone, check=True)


class TestNoKeyKeepsTheDefaultBranchLabel:
    def test_reports_default_branch_and_no_base_branch_line(self, tmp_path):
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/plain")
        subprocess.run(["git", "checkout", "-q", "feat/plain"], cwd=local, check=True)

        result = _run_script(local)

        assert result.returncode == 0
        assert "Default branch: main" in result.stdout
        assert "Base branch:" not in result.stdout


class TestStackBaseKey:
    def test_reports_base_branch_and_in_sync_with_the_stack_base(self, tmp_path):
        local, _bare = _make_stacked_repo(tmp_path)

        result = _run_script(local)

        assert result.returncode == 0, result.stderr
        assert "Base branch: stack" in result.stdout
        assert "Default branch:" not in result.stdout
        assert "Behind: 0" in result.stdout
        assert "In sync with origin/stack." in result.stdout

    def test_behind_count_is_measured_against_the_stack_base(self, tmp_path):
        local, bare = _make_stacked_repo(tmp_path)
        _advance_remote_stack(bare, tmp_path)

        result = _run_script(local)

        assert result.returncode == 0, result.stderr
        assert "Base branch: stack" in result.stdout
        assert "Behind: 1" in result.stdout
        assert "CLEAN" in result.stdout

    def test_behind_count_is_measured_under_a_narrowed_fetch_refspec(self, tmp_path):
        """A refspec narrowed to main leaves a plain `git fetch origin stack`
        unable to update origin/stack, so the script's named destination is
        what makes the count current."""
        local, bare = _make_stacked_repo(tmp_path)
        subprocess.run(
            ["git", "config", "remote.origin.fetch", "+refs/heads/main:refs/remotes/origin/main"],
            cwd=local, check=True,
        )
        _advance_remote_stack(bare, tmp_path)

        result = _run_script(local)

        assert result.returncode == 0, result.stderr
        assert "Behind: 1" in result.stdout

    def test_stack_base_deleted_on_origin_stops_even_with_its_local_ref_present(self, tmp_path):
        local, bare = _make_stacked_repo(tmp_path)
        subprocess.run(["git", "branch", "-D", "stack"], cwd=bare, check=True)

        result = _run_script(local)

        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr.count("\n") == 1
        assert result.stderr.startswith("branch-divergence-status.sh:")
        assert f"branch.{_STACKED_BRANCH}.gh-merge-base" in result.stderr
        assert "fetch failed" in result.stderr
        assert "retry the command before changing the key" in result.stderr
        assert 'run "git ls-remote origin refs/heads/stack":' in result.stderr
        assert "if it prints nothing, the base is not on origin" in result.stderr
        assert '"git fetch origin +refs/heads/stack:refs/remotes/origin/stack" by hand' in result.stderr
        assert "is stack, which cannot be used as the stack base" in result.stderr
        assert "The engineer runs the fix, not the agent" in result.stderr

    def test_verify_stops_when_a_fetch_reports_success_but_the_ref_is_missing(self, tmp_path):
        """The verify, not the fetch status, is what keeps an unresolvable ref from being measured."""
        local, _bare = _make_stacked_repo(tmp_path)
        subprocess.run(["git", "update-ref", "-d", "refs/remotes/origin/stack"], cwd=local, check=True)
        bin_dir = tmp_path / "bin-fetch-succeeds-silently"
        _make_git_exiting_with_status(bin_dir, "fetch", 0)
        env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "REAL_GIT": shutil.which("git")}

        result = _run_script(local, env=env)

        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr.startswith("branch-divergence-status.sh:")
        assert "fetch failed" in result.stderr

    def test_local_branch_named_like_the_remote_tracking_ref_does_not_replace_the_base(self, tmp_path):
        """A local branch `origin/stack` outranks `origin/stack` in git's short-name lookup, so
        the script must measure through the fully qualified remote-tracking ref."""
        local, _bare = _make_stacked_repo(tmp_path)
        subprocess.run(["git", "checkout", "-q", "--detach", "main"], cwd=local, check=True)
        _commit(local, "unreachable from the stacked branch")
        subprocess.run(["git", "branch", "origin/stack"], cwd=local, check=True)
        subprocess.run(["git", "checkout", "-q", _STACKED_BRANCH], cwd=local, check=True)

        result = _run_script(local)

        assert result.returncode == 0, result.stderr
        assert "Behind: 0" in result.stdout

    def test_trial_merge_reads_the_remote_tracking_ref_not_a_shadow_local_branch(self, tmp_path):
        """A local branch `origin/stack` at a non-conflicting commit would make a short-name
        trial merge report CLEAN, so the conflicting real stack tip must still report CONFLICT."""
        local, bare = _make_stacked_repo(tmp_path)
        (local / "conflict.txt").write_text("local content\n")
        subprocess.run(["git", "add", "conflict.txt"], cwd=local, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "local conflict.txt"], cwd=local, check=True)
        clone = tmp_path / "conflicting-stack-clone"
        subprocess.run(["git", "clone", "-q", "-b", "stack", str(bare), str(clone)], check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=clone, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=clone, check=True)
        (clone / "conflict.txt").write_text("remote content\n")
        subprocess.run(["git", "add", "conflict.txt"], cwd=clone, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "remote conflict.txt"], cwd=clone, check=True)
        subprocess.run(["git", "push", "-q", "origin", "stack"], cwd=clone, check=True)
        subprocess.run(["git", "branch", "origin/stack", "main"], cwd=local, check=True)

        result = _run_script(local)

        assert result.returncode == 0, result.stderr
        assert "Behind: 1" in result.stdout
        assert "CONFLICT" in result.stdout
        assert "conflict.txt" in result.stdout

    def test_invalid_key_stops_with_this_scripts_prefix(self, tmp_path):
        local, _bare = _make_stacked_repo(tmp_path, key_value="-stack")

        result = _run_script(local)

        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr.startswith("branch-divergence-status.sh:")
        assert f"branch.{_STACKED_BRANCH}.gh-merge-base" in result.stderr
        assert "starts with '-'" in result.stderr

    def test_force_pushed_stack_base_updates_to_the_new_tip(self, tmp_path):
        """The fetch refspec's leading + lets a rewritten stack base replace
        a tip that is not an ancestor of the new one."""
        local, bare = _make_stacked_repo(tmp_path)
        clone = tmp_path / "rewrite-stack-clone"
        subprocess.run(["git", "clone", "-q", "-b", "stack", str(bare), str(clone)], check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=clone, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=clone, check=True)
        subprocess.run(["git", "reset", "-q", "--hard", "HEAD~1"], cwd=clone, check=True)
        (clone / "rewritten.txt").write_text("rewritten stack work\n")
        subprocess.run(["git", "add", "rewritten.txt"], cwd=clone, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "rewritten stack"], cwd=clone, check=True)
        subprocess.run(["git", "push", "-q", "--force", "origin", "stack"], cwd=clone, check=True)

        result = _run_script(local)

        assert result.returncode == 0, result.stderr
        assert "Base branch: stack" in result.stdout
        assert "Behind: 1" in result.stdout

    def test_origin_prefixed_key_stops_naming_the_prefix(self, tmp_path):
        local, _bare = _make_stacked_repo(tmp_path, key_value="origin/stack")

        result = _run_script(local)

        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr.startswith("branch-divergence-status.sh:")
        assert "starts with 'origin/'" in result.stderr

    def test_key_set_still_reports_the_base_when_origin_head_is_unset(self, tmp_path):
        local, _bare = _make_stacked_repo(tmp_path)
        subprocess.run(["git", "remote", "set-head", "origin", "--delete"], cwd=local, check=True)

        result = _run_script(local)

        assert result.returncode == 0, result.stderr
        assert "Base branch: stack" in result.stdout
